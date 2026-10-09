"use client";

import { useEffect, useState, type FormEvent } from "react";
import type {
  ConnectionDefaults,
  CreateConnectionProfileInput,
  DefaultClusterDatabasesResult,
  ListClusterDatabasesInput
} from "@/lib/product-api/types";

interface CreateConnectionProfileOverlayProps {
  isCreating: boolean;
  errorMessage: string | null;
  onCancel: () => void;
  onSubmit: (input: CreateConnectionProfileInput) => Promise<void>;
  /** Two-step connect, part 1: enumerate databases on the cluster from the
   * supplied credentials so the user can pick one instead of typing it. */
  onListDatabases: (
    input: ListClusterDatabasesInput
  ) => Promise<{ endpoint: string; databases: string[] }>;
  /** Best-effort prefill of the form from the deployment's environment
   * (endpoint/user/database/SSL/mode + password env-var name). Never carries
   * the password value. Optional — the form keeps its placeholders if absent
   * or if the call fails. */
  onLoadDefaults?: () => Promise<ConnectionDefaults>;
  /** Zero-config connect: list databases on the cluster the server is already
   * configured for, with no credentials from the browser. When this resolves,
   * the overlay skips straight to picking a database. Rejection (no
   * ARANGO_ENDPOINT, unreachable cluster, demo mode) falls back to the
   * explicit credentials form. */
  onListDefaultClusterDatabases?: () => Promise<DefaultClusterDatabasesResult>;
}

const deploymentModes = [
  { value: "local", label: "Local" },
  { value: "self_managed", label: "Self Managed" },
  { value: "arangograph", label: "ArangoGraph" },
  { value: "amp", label: "AMP" }
];

export function CreateConnectionProfileOverlay({
  isCreating,
  errorMessage,
  onCancel,
  onSubmit,
  onListDatabases,
  onLoadDefaults,
  onListDefaultClusterDatabases
}: CreateConnectionProfileOverlayProps) {
  const [form, setForm] = useState<CreateConnectionProfileInput>({
    name: "",
    deploymentMode: "local",
    endpoint: "http://localhost:8529",
    database: "",
    username: "",
    verifySsl: true,
    passwordSecretEnvVar: "",
    login: "password"
  });
  const [prefilled, setPrefilled] = useState(false);
  // "detecting" until we know whether this deployment can connect on its own.
  // "default-cluster" is the zero-config path; "manual" is the credentials
  // form, reached either by fallback or by the user asking for another cluster.
  const [connectMode, setConnectMode] = useState<
    "detecting" | "default-cluster" | "manual"
  >(onListDefaultClusterDatabases ? "detecting" : "manual");
  const [defaultCluster, setDefaultCluster] =
    useState<DefaultClusterDatabasesResult | null>(null);
  // Once the user edits the name we stop deriving it from the database.
  const [nameTouched, setNameTouched] = useState(false);

  // Prefill from the deployment environment on open. Best-effort: only fills
  // fields the user hasn't already changed, and silently keeps placeholders
  // if there are no defaults or the lookup fails.
  useEffect(() => {
    // Also relies on a memoised prop: when this dependency was unstable the
    // effect re-fetched on every render and overwrote endpoint / username
    // after the user had edited them.
    if (!onLoadDefaults) {
      return;
    }
    let cancelled = false;
    onLoadDefaults()
      .then((defaults) => {
        if (cancelled) {
          return;
        }
        setForm((current) => ({
          ...current,
          endpoint: defaults.endpoint || current.endpoint,
          username: defaults.username || current.username,
          database: defaults.database || current.database,
          verifySsl: defaults.verifySsl,
          deploymentMode: defaults.deploymentMode || current.deploymentMode,
          passwordSecretEnvVar:
            defaults.passwordSecretEnvVar || current.passwordSecretEnvVar
        }));
        setPrefilled(
          Boolean(defaults.endpoint || defaults.username || defaults.database)
        );
      })
      .catch(() => {
        /* best-effort — keep the form's own placeholders */
      });
    return () => {
      cancelled = true;
    };
  }, [onLoadDefaults]);
  const [databases, setDatabases] = useState<string[]>([]);
  const [isFinding, setIsFinding] = useState(false);
  const [findError, setFindError] = useState<string | null>(null);
  const [hasSearched, setHasSearched] = useState(false);

  // Zero-config connect. The endpoint, username and password env-var name all
  // come from the server's environment already — the old form asked an
  // operator to retype what `onLoadDefaults` had just handed it, which is not
  // a security boundary (the password is resolved server-side either way) and
  // made a variable-name field read like a password prompt. Try the server's
  // own cluster first and fall back to asking only if that fails.
  useEffect(() => {
    // Runs once: `onListDefaultClusterDatabases` is memoised by the hook that
    // supplies it. That stability matters — an unstable identity re-ran this
    // effect on every render and forced connectMode back to "default-cluster",
    // silently undoing "Connect to a different cluster…" as soon as it was
    // clicked.
    if (!onListDefaultClusterDatabases) {
      return;
    }
    let cancelled = false;
    onListDefaultClusterDatabases()
      .then((result) => {
        if (cancelled) {
          return;
        }
        setDefaultCluster(result);
        setDatabases(result.databases);
        setHasSearched(true);
        setConnectMode("default-cluster");
        setForm((current) => ({
          ...current,
          endpoint: result.endpoint || current.endpoint,
          username: result.username || current.username,
          verifySsl: result.verifySsl,
          deploymentMode: result.deploymentMode || current.deploymentMode,
          database: result.databases[0] ?? "",
          // NFR-24: on the platform the profile stores no secret; whoever
          // uses it acts as themselves.
          login: result.login
        }));
      })
      .catch(() => {
        if (!cancelled) {
          setConnectMode("manual");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [onListDefaultClusterDatabases]);

  // Name is required, and on the zero-config path the database is the only
  // thing the user actually chose — so default to it rather than making them
  // invent a label. Stops as soon as they type their own.
  useEffect(() => {
    if (nameTouched || !form.database) {
      return;
    }
    setForm((current) =>
      current.name === current.database
        ? current
        : { ...current, name: current.database }
    );
  }, [form.database, nameTouched]);

  const updateField = <K extends keyof CreateConnectionProfileInput>(
    key: K,
    value: CreateConnectionProfileInput[K]
  ) => setForm((current) => ({ ...current, [key]: value }));

  // Changing any credential invalidates a prior database search so the user
  // can't save a database picked against different credentials.
  const updateCredField = <K extends keyof CreateConnectionProfileInput>(
    key: K,
    value: CreateConnectionProfileInput[K]
  ) => {
    setForm((current) => ({
      ...current,
      [key]: value,
      // Changing credentials invalidates a prior database pick.
      ...(hasSearched ? { database: "" } : {})
    }));
    if (hasSearched) {
      setHasSearched(false);
      setDatabases([]);
    }
  };

  const canSearch =
    !isFinding &&
    form.endpoint.trim().length > 0 &&
    form.username.trim().length > 0 &&
    (form.passwordSecretEnvVar ?? "").trim().length > 0;

  async function handleFindDatabases() {
    setFindError(null);
    setIsFinding(true);
    try {
      const result = await onListDatabases({
        endpoint: form.endpoint.trim(),
        username: form.username.trim(),
        passwordSecretEnvVar: (form.passwordSecretEnvVar ?? "").trim(),
        verifySsl: form.verifySsl
      });
      setDatabases(result.databases);
      setHasSearched(true);
      // Auto-select the first database so the form is immediately submittable.
      if (result.databases.length > 0) {
        updateField("database", result.databases[0]);
      } else {
        updateField("database", "");
      }
    } catch (error) {
      setFindError(
        error instanceof Error ? error.message : "Failed to list databases"
      );
      setDatabases([]);
      setHasSearched(true);
    } finally {
      setIsFinding(false);
    }
  }

  const submitDisabled =
    isCreating || form.name.trim().length === 0 || form.database.trim().length === 0;

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitDisabled) {
      return;
    }
    await onSubmit(form);
  }

  return (
    <div className="confirmation-backdrop" role="presentation" onClick={onCancel}>
      <form
        className="connection-profile-overlay"
        aria-label="Create connection profile"
        onClick={(event) => event.stopPropagation()}
        onSubmit={handleSubmit}
      >
        <header>
          <div>
            <p className="muted">
              {connectMode === "default-cluster"
                ? "Workspace Setup · Pick a database"
                : "Workspace Setup · Step 1: Cluster credentials"}
            </p>
            <h2>Connect to a Cluster</h2>
          </div>
          <button
            className="secondary-button"
            type="button"
            disabled={isCreating}
            onClick={onCancel}
          >
            Close
          </button>
        </header>

        {connectMode === "detecting" ? (
          <p className="muted">Checking this deployment&apos;s cluster…</p>
        ) : null}

        {connectMode === "default-cluster" &&
        defaultCluster &&
        defaultCluster.login === "platform" ? (
          <>
            <p className="muted">
              Signed in as <code>{defaultCluster.username}</code> with your
              platform login. Pick one of the databases you have access to.
            </p>
            <p className="muted">
              The profile stores no password: everyone who uses it works with
              their own platform login and permissions.
            </p>
          </>
        ) : null}

        {connectMode === "default-cluster" &&
        defaultCluster &&
        defaultCluster.login !== "platform" ? (
          <>
            <p className="muted">
              Connected to <code>{defaultCluster.endpoint}</code> as{" "}
              <code>{defaultCluster.username}</code>
              {defaultCluster.verifySsl ? "" : " (SSL verification off)"}. Pick a
              database below.
            </p>
            <p className="muted">
              Credentials come from this deployment&apos;s environment, so
              there is nothing to enter.
            </p>
          </>
        ) : null}

        {connectMode === "manual" ? (
          <>
            <p className="muted">
              Enter cluster credentials and click <strong>Find databases</strong>{" "}
              to list what&apos;s available, then pick a database. The password
              is referenced by environment-variable name — never entered in
              plaintext.
            </p>
            {prefilled ? (
              <p className="muted">
                Prefilled from the server environment (<code>.env</code>). Edit
                any field as needed — the password is never read, only
                referenced by variable name.
              </p>
            ) : null}
          </>
        ) : null}

        <label>
          Name
          <input
            required
            value={form.name}
            onChange={(event) => {
              setNameTouched(true);
              updateField("name", event.target.value);
            }}
          />
        </label>
        {connectMode === "manual" ? (
          <>
        <label>
          Deployment Mode
          <select
            value={form.deploymentMode}
            onChange={(event) => updateField("deploymentMode", event.target.value)}
          >
            {deploymentModes.map((mode) => (
              <option key={mode.value} value={mode.value}>
                {mode.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Endpoint
          <input
            required
            value={form.endpoint}
            onChange={(event) => updateCredField("endpoint", event.target.value)}
          />
        </label>
        <label>
          Username
          <input
            required
            value={form.username}
            onChange={(event) => updateCredField("username", event.target.value)}
          />
        </label>
        <label>
          Password Environment Variable
          <input
            placeholder="ARANGO_PASSWORD"
            value={form.passwordSecretEnvVar}
            onChange={(event) =>
              updateCredField("passwordSecretEnvVar", event.target.value)
            }
          />
        </label>
        <label className="checkbox-row">
          <input
            type="checkbox"
            checked={form.verifySsl}
            onChange={(event) => updateCredField("verifySsl", event.target.checked)}
          />
          Verify SSL
        </label>

        <div className="confirmation-actions" style={{ justifyContent: "flex-start" }}>
          <button
            className="secondary-button"
            type="button"
            disabled={!canSearch}
            title={canSearch ? "List databases on this cluster" : "Enter endpoint, username, and password env var first"}
            onClick={handleFindDatabases}
          >
            {isFinding ? "Finding..." : "Find databases"}
          </button>
        </div>
          </>
        ) : null}

        {findError ? <p className="error-text">{findError}</p> : null}

        {/* Step 2: pick a database from the discovered list. */}
        {hasSearched ? (
          databases.length > 0 ? (
            <label>
              {connectMode === "default-cluster" ? "Database" : "Database (Step 2)"}
              <select
                value={form.database}
                onChange={(event) => updateField("database", event.target.value)}
              >
                {databases.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
            </label>
          ) : form.login === "platform" ? (
            <p className="muted">
              Your platform account has access to no databases yet. Ask an
              administrator to grant you access to the database you want to
              analyse.
            </p>
          ) : (
            <p className="muted">
              No databases visible to these credentials. Check the endpoint and
              that the account can list databases (root / <code>_system</code>).
            </p>
          )
        ) : null}

        {connectMode === "default-cluster" ? (
          <div className="confirmation-actions" style={{ justifyContent: "flex-start" }}>
            <button
              className="secondary-button"
              type="button"
              disabled={isCreating}
              title="Enter credentials for a cluster other than this deployment's own"
              onClick={() => {
                setConnectMode("manual");
                setDefaultCluster(null);
                setHasSearched(false);
                setDatabases([]);
                updateField("database", "");
                // Another cluster has no platform login: back to a password.
                updateField("login", "password");
              }}
            >
              Connect to a different cluster…
            </button>
          </div>
        ) : null}

        {errorMessage ? <p className="error-text">{errorMessage}</p> : null}

        <div className="confirmation-actions">
          <button
            className="secondary-button"
            type="button"
            disabled={isCreating}
            onClick={onCancel}
          >
            Cancel
          </button>
          <button
            className="primary-button"
            type="submit"
            disabled={submitDisabled}
            title={
              form.database.trim().length === 0
                ? "Find and select a database first"
                : "Save connection profile"
            }
          >
            {isCreating ? "Creating..." : "Create Profile"}
          </button>
        </div>
      </form>
    </div>
  );
}
