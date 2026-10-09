import { describe, expect, it, vi } from "vitest";

import {
  connectionSecretRefs,
  createProductAPIClient,
  mapConnectionLogin,
  mapReportBundle,
  mapWorkflowDAGView,
  mapWorkspaceHealth,
  mapWorkspaceOverview,
  resolveArtifactAsset,
  workspaceAssetsFromOverview
} from "../client";
import type { WorkspaceAsset } from "../types";

describe("product API client mappers", () => {
  it("creates workspaces through the product API", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        workspace_id: "workspace-1",
        customer_name: "Example",
        project_name: "Graph Analytics",
        environment: "dev",
        description: "Demo workspace",
        status: "active",
        tags: ["demo"]
      })
    });
    vi.stubGlobal("fetch", fetchMock);

    const workspace = await createProductAPIClient("http://api.example").createWorkspace({
      customerName: "Example",
      projectName: "Graph Analytics",
      environment: "dev",
      description: "Demo workspace",
      tags: ["demo"],
      actor: "tester"
    });

    expect(workspace).toMatchObject({
      workspaceId: "workspace-1",
      customerName: "Example",
      projectName: "Graph Analytics",
      environment: "dev"
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://api.example/api/workspaces",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          customer_name: "Example",
          project_name: "Graph Analytics",
          environment: "dev",
          description: "Demo workspace",
          tags: ["demo"],
          actor: "tester"
        })
      })
    );

    vi.unstubAllGlobals();
  });

  it("surfaces the server's explanation, not just the status code", async () => {
    // The API answers domain failures with {"error", "detail"}; the detail is
    // the useful half — which graph profile blocks a delete. Throwing only the
    // status reduced every refusal to "409 Conflict".
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 409,
      statusText: "Conflict",
      json: async () => ({
        error: "ConflictError",
        detail:
          "Connection profile 'addtech-prod-demo' still has 2 graph profile(s) "
          + "using it: 'default', 'AdtechGraph'. Delete those first."
      })
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      createProductAPIClient("http://api.example").deleteConnectionProfile("c-1")
    ).rejects.toThrow(/still has 2 graph profile\(s\) using it/);
  });

  it("falls back to the status line when there is no JSON body", async () => {
    // A proxy error page or dropped connection has no detail to show.
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 502,
      statusText: "Bad Gateway",
      json: async () => {
        throw new Error("not json");
      }
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      createProductAPIClient("http://api.example").deleteConnectionProfile("c-1")
    ).rejects.toThrow("Product API request failed: 502 Bad Gateway");
  });

  it("lists default-cluster databases without sending any credentials", async () => {
    // Zero-config connect: the browser sends an empty body because the server
    // already holds the endpoint, username and password for its own cluster.
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        endpoint: "https://cluster.example:8529",
        databases: ["alpha", "beta"],
        username: "svc-account",
        verify_ssl: false,
        deployment_mode: "self_managed"
      })
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await createProductAPIClient(
      "http://api.example"
    ).listDefaultClusterDatabases();

    expect(result).toEqual({
      endpoint: "https://cluster.example:8529",
      databases: ["alpha", "beta"],
      username: "svc-account",
      verifySsl: false,
      deploymentMode: "self_managed",
      login: "password"
    });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(
      "http://api.example/api/connections/default-cluster/databases"
    );
    // The payload must stay empty — sending credentials from the browser is
    // the thing this endpoint exists to avoid.
    expect(JSON.parse(init.body)).toEqual({});
  });

  it("reports the platform login for default-cluster databases on the platform", async () => {
    // NFR-24: on the Arango platform the server lists the databases the
    // signed-in user can use, and says the profile should use their login.
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        endpoint: "https://platform.example",
        databases: ["sales"],
        username: "alice",
        verify_ssl: true,
        deployment_mode: "",
        login: "platform",
        secret_ref: { kind: "platform_login" }
      })
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await createProductAPIClient(
      "http://api.example"
    ).listDefaultClusterDatabases();

    expect(result.login).toBe("platform");
    expect(result.username).toBe("alice");
    vi.unstubAllGlobals();
  });

  it("treats an unknown or missing login kind as the password path", () => {
    expect(mapConnectionLogin(undefined)).toBe("password");
    expect(mapConnectionLogin("kerberos")).toBe("password");
    expect(mapConnectionLogin("platform")).toBe("platform");
  });

  it("stores no secret reference for a platform-login profile", () => {
    const base = {
      name: "sales",
      deploymentMode: "",
      endpoint: "https://platform.example",
      database: "sales",
      username: "alice",
      verifySsl: true
    };
    expect(
      connectionSecretRefs({
        ...base,
        login: "platform",
        passwordSecretEnvVar: "ARANGO_PASSWORD"
      })
    ).toEqual({ password: { kind: "platform_login" } });
    expect(
      connectionSecretRefs({ ...base, passwordSecretEnvVar: " ARANGO_PASSWORD " })
    ).toEqual({ password: { kind: "env", ref: "ARANGO_PASSWORD" } });
    expect(connectionSecretRefs({ ...base, login: "password" })).toEqual({});
  });

  it("maps workspace overview payloads into UI assets", () => {
    const overview = mapWorkspaceOverview({
      workspace: {
        workspace_id: "workspace-1",
        customer_name: "Example",
        project_name: "Graph Analytics",
        environment: "dev"
      },
      counts: { workflow_runs: 1, reports: 1 },
      latest_connection_profiles: [
        {
          connection_profile_id: "connection-1",
          workspace_id: "workspace-1",
          name: "Development",
          deployment_mode: "local",
          endpoint: "http://localhost:8529",
          database: "customer_graph",
          username: "root",
          last_verification_status: "unknown"
        }
      ],
      latest_graph_profiles: [
        {
          graph_profile_id: "graph-profile-1",
          workspace_id: "workspace-1",
          connection_profile_id: "connection-1",
          graph_name: "CustomerGraph",
          status: "active",
          version: 2,
          vertex_collections: ["customers"],
          edge_collections: ["transactions"],
          counts: { customers: 10 }
        }
      ],
      latest_source_documents: [
        {
          document_id: "document-1",
          workspace_id: "workspace-1",
          filename: "requirements.md",
          mime_type: "text/markdown",
          sha256: "abc123",
          storage_mode: "inline_text",
          extracted_text: "Requirements content",
          metadata: { source: "upload" }
        }
      ],
      latest_requirement_versions: [
        {
          requirement_version_id: "requirement-version-1",
          workspace_id: "workspace-1",
          version: 1,
          status: "approved",
          summary: "Approved BRD",
          objectives: [],
          requirements: [],
          constraints: []
        }
      ],
      latest_workflow_runs: [
        {
          run_id: "run-1",
          status: "running",
          workflow_mode: "agentic"
        }
      ],
      latest_reports: [
        {
          report_id: "report-1",
          title: "Risk Report",
          status: "draft"
        }
      ]
    });

    expect(overview.latestWorkflowRuns[0].run_id).toBe("run-1");
    expect(overview.latestConnectionProfiles[0]).toMatchObject({
      connectionProfileId: "connection-1",
      name: "Development",
      deploymentMode: "local"
    });
    expect(overview.latestGraphProfiles[0]).toMatchObject({
      graphProfileId: "graph-profile-1",
      graphName: "CustomerGraph",
      vertexCollections: ["customers"],
      counts: { customers: 10 }
    });
    expect(overview.latestSourceDocuments[0]).toMatchObject({
      documentId: "document-1",
      filename: "requirements.md",
      storageMode: "inline_text",
      metadata: { source: "upload" }
    });
    expect(overview.latestRequirementVersions[0]).toMatchObject({
      requirementVersionId: "requirement-version-1",
      version: 1,
      status: "approved"
    });
    expect(workspaceAssetsFromOverview(overview)).toEqual([
      {
        id: "connection-1",
        kind: "connection-profile",
        label: "Development",
        // The profile name rarely matches the database it reaches, so the row
        // names the database too.
        description: "local connection (unknown) · customer_graph"
      },
      {
        id: "graph-profile-1",
        kind: "graph-profile",
        label: "CustomerGraph",
        description: "Graph profile (active) · customer_graph"
      },
      {
        id: "requirements:workspace-1",
        kind: "requirements",
        label: "Requirements",
        description: "v1 (approved)"
      },
      {
        id: "document-1",
        kind: "document",
        label: "requirements.md",
        description: "text/markdown"
      },
      {
        id: "run-1",
        kind: "run",
        label: "Run run-1",
        description: "agentic workflow (running)"
      },
      {
        id: "report-1",
        kind: "report",
        label: "Risk Report",
        description: "Report (draft)"
      },
      {
        // FR-45..FR-48: always present, even with no executions — this row is
        // the only entry point to the catalog.
        id: "analysis-catalog:workspace-1",
        kind: "analysis-catalog",
        label: "Analysis Catalog",
        description: "Executions, epochs, comparison, and lineage"
      },
      {
        // FR-19..FR-26: likewise the only entry point for authoring and
        // reviewing use cases and templates.
        id: "use-cases:workspace-1",
        kind: "use-cases",
        label: "Use Cases & Templates",
        description: "Author, review, and version analysis templates"
      },
      {
        // FR-54: retention is an admin surface, so it is shown even when no
        // policy is configured — "nothing configured" is the state an admin
        // most needs to discover.
        id: "retention:workspace-1",
        kind: "retention",
        label: "Retention",
        description: "Configure retention windows and preview a sweep"
      }
    ]);
  });

  it("consolidates multiple RequirementVersion records into one Requirements asset", () => {
    // Regression test for the IA refactor: the AssetExplorer should never
    // grow one row per approved/superseded version. Instead, it surfaces a
    // single "Requirements" row whose description names the active version
    // and counts the prior ones; the canvas-side dropdown is responsible for
    // exposing history.
    const overview = mapWorkspaceOverview({
      workspace: { workspace_id: "workspace-2", customer_name: "Acme", project_name: "Households", environment: "prod" },
      counts: {},
      latest_connection_profiles: [],
      latest_graph_profiles: [],
      latest_source_documents: [],
      latest_requirement_versions: [
        {
          requirement_version_id: "requirement-version-2",
          workspace_id: "workspace-2",
          version: 2,
          status: "approved",
          summary: "Refined v2",
          objectives: [],
          requirements: [],
          constraints: []
        },
        {
          requirement_version_id: "requirement-version-1",
          workspace_id: "workspace-2",
          version: 1,
          status: "superseded",
          summary: "Initial",
          objectives: [],
          requirements: [],
          constraints: []
        }
      ],
      latest_workflow_runs: [],
      latest_reports: []
    });

    const assets = workspaceAssetsFromOverview(overview);
    const requirementsAssets = assets.filter((asset) => asset.kind === "requirements");
    expect(requirementsAssets).toHaveLength(1);
    expect(requirementsAssets[0]).toEqual({
      id: "requirements:workspace-2",
      kind: "requirements",
      label: "Requirements",
      description: "v2 (approved) · 1 prior version"
    });
  });

  it("omits the Requirements asset when the workspace has no versions yet", () => {
    const overview = mapWorkspaceOverview({
      workspace: { workspace_id: "workspace-3", customer_name: "Acme", project_name: "Empty", environment: "prod" },
      counts: {},
      latest_connection_profiles: [],
      latest_graph_profiles: [],
      latest_source_documents: [],
      latest_requirement_versions: [],
      latest_workflow_runs: [],
      latest_reports: []
    });

    const assets = workspaceAssetsFromOverview(overview);
    expect(assets.find((asset) => asset.kind === "requirements")).toBeUndefined();
  });

  it("maps workflow DAG payloads into canvas-friendly shape", () => {
    // Regression test: the backend (ProductService._workflow_step_node)
    // sends full artifact_refs/warnings/errors arrays plus timing/retry/
    // checkpoint/cost detail — never artifact_count/warning_count/
    // error_count. The mapper must derive counts from the arrays and
    // pass the full detail through for the FloatingDetailPanel (FR-36/37).
    const dag = mapWorkflowDAGView({
      run_id: "run-1",
      workspace_id: "workspace-1",
      status: "running",
      workflow_mode: "agentic",
      nodes: [
        {
          id: "step-1",
          label: "Extract",
          status: "completed",
          agent_name: "extractor",
          started_at: "2026-01-01T00:00:00Z",
          completed_at: "2026-01-01T00:01:00Z",
          duration_ms: 60000,
          retry_count: 1,
          checkpoint_id: "checkpoint-1",
          inputs: { source: "graph-profile-1" },
          outputs: { rows: 42 },
          artifact_refs: [
            { type: "requirement_version", id: "rv-1" },
            { type: "report", id: "report-1" }
          ],
          warnings: ["Sample warning"],
          errors: [],
          cost: { tokens: 100 }
        }
      ],
      edges: [{ id: "edge-1", from: "step-1", to: "step-2" }],
      warnings: [],
      errors: []
    });

    expect(dag.runId).toBe("run-1");
    expect(dag.nodes[0]).toMatchObject({
      id: "step-1",
      agentName: "extractor",
      artifactCount: 2,
      warningCount: 1,
      errorCount: 0,
      startedAt: "2026-01-01T00:00:00Z",
      completedAt: "2026-01-01T00:01:00Z",
      durationMs: 60000,
      retryCount: 1,
      checkpointId: "checkpoint-1",
      inputs: { source: "graph-profile-1" },
      outputs: { rows: 42 },
      artifactRefs: [
        { type: "requirement_version", id: "rv-1" },
        { type: "report", id: "report-1" }
      ],
      warningMessages: ["Sample warning"],
      cost: { tokens: 100 }
    });
    expect(dag.edges[0]).toEqual({ id: "edge-1", from: "step-1", to: "step-2" });
  });

  it("defaults workflow DAG node counts to zero when detail arrays are omitted", () => {
    const dag = mapWorkflowDAGView({
      run_id: "run-1",
      workspace_id: "workspace-1",
      status: "queued",
      workflow_mode: "traditional",
      nodes: [{ id: "step-1", label: "Extract", status: "pending" }],
      edges: [],
      warnings: [],
      errors: []
    });

    expect(dag.nodes[0]).toMatchObject({
      artifactCount: 0,
      warningCount: 0,
      errorCount: 0
    });
  });

  it("maps workspace health payloads into explorer-ready shape", () => {
    const health = mapWorkspaceHealth({
      workspace_id: "workspace-1",
      status: "needs_attention",
      counts: { connection_profiles: 0 },
      issues: [
        {
          severity: "warning",
          code: "missing_connection_profile",
          message: "Workspace has no connection profiles.",
          entity_ids: ["connection-1"]
        }
      ]
    });

    expect(health.workspaceId).toBe("workspace-1");
    expect(health.issues[0]).toEqual({
      severity: "warning",
      code: "missing_connection_profile",
      message: "Workspace has no connection profiles.",
      entityIds: ["connection-1"]
    });
  });

  it("maps and orders report bundles for dynamic rendering", () => {
    const report = mapReportBundle({
      manifest: {
        report_id: "report-1",
        workspace_id: "workspace-1",
        run_id: "run-1",
        title: "Risk Report",
        status: "draft",
        summary: "Summary",
        version: 2
      },
      sections: [
        {
          section_id: "section-2",
          order: 2,
          type: "recommendation",
          title: "Recommendation",
          content: { text: "Act" }
        },
        {
          section_id: "section-1",
          order: 1,
          type: "summary",
          title: "Summary",
          content: { text: "Read" }
        }
      ],
      charts: [
        {
          chart_id: "chart-1",
          title: "Counts",
          chart_type: "table",
          data: { rows: [] }
        }
      ],
      snapshots: []
    });

    expect(report.manifest.reportId).toBe("report-1");
    expect(report.sections.map((section) => section.sectionId)).toEqual([
      "section-1",
      "section-2"
    ]);
    expect(report.charts[0]).toMatchObject({
      chartId: "chart-1",
      chartType: "table"
    });
  });

  it("publishes reports through the product API", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        manifest: {
          report_id: "report-1",
          workspace_id: "workspace-1",
          run_id: "run-1",
          title: "Risk Report",
          status: "published"
        },
        sections: [],
        charts: [],
        snapshots: []
      })
    });
    vi.stubGlobal("fetch", fetchMock);

    const report = await createProductAPIClient("http://api.example").publishReport(
      "report-1",
      "tester"
    );

    expect(report.manifest.status).toBe("published");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://api.example/api/reports/report-1/publish",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ actor: "tester" })
      })
    );

    vi.unstubAllGlobals();
  });

  it("exports a report as Markdown via the product API and surfaces filename + blob", async () => {
    // FR-42: the export endpoint returns raw text with a filename in the
    // Content-Disposition header. The client must parse the header so the
    // browser-side download uses the same filename the server picked.
    const markdown = "# Risk Report\n\nFindings.\n";
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      headers: {
        get: (header: string) =>
          header.toLowerCase() === "content-disposition"
            ? 'attachment; filename="risk-report.md"'
            : null
      },
      blob: async () => new Blob([markdown], { type: "text/markdown" }),
      text: async () => markdown,
      statusText: "OK",
      status: 200
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await createProductAPIClient("http://api.example").exportReport(
      "report-1",
      "markdown"
    );

    expect(result.filename).toBe("risk-report.md");
    expect(result.format).toBe("markdown");
    expect(await result.blob.text()).toBe(markdown);
    expect(fetchMock).toHaveBeenCalledWith(
      "http://api.example/api/reports/report-1/export?format=markdown",
      { method: "GET" }
    );

    vi.unstubAllGlobals();
  });

  it("falls back to a deterministic export filename when the server omits Content-Disposition", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      headers: { get: () => null },
      blob: async () => new Blob(["<html/>"], { type: "text/html" }),
      text: async () => "<html/>",
      statusText: "OK",
      status: 200
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await createProductAPIClient("http://api.example").exportReport(
      "report-7",
      "html"
    );

    expect(result.filename).toBe("report-report-7.html");
    expect(result.format).toBe("html");

    vi.unstubAllGlobals();
  });

  it("PATCHes only the workspace fields the caller actually set", async () => {
    // FR-1 (Edit Workspace): the API client must omit unset fields so the
    // backend can distinguish "patch to empty" from "leave alone". Sending
    // every field every time would push the diff cost onto the backend.
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        workspace_id: "workspace-1",
        customer_name: "Acme Corp",
        project_name: "AdTech",
        environment: "dev",
        description: "Demo",
        status: "active",
        tags: ["adtech", "demo"]
      })
    });
    vi.stubGlobal("fetch", fetchMock);

    const updated = await createProductAPIClient(
      "http://api.example"
    ).updateWorkspace("workspace-1", {
      customerName: "Acme Corp",
      tags: ["adtech", "demo"],
      actor: "ops@example.com"
    });

    expect(updated).toMatchObject({
      workspaceId: "workspace-1",
      customerName: "Acme Corp",
      tags: ["adtech", "demo"]
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://api.example/api/workspaces/workspace-1",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({
          customer_name: "Acme Corp",
          tags: ["adtech", "demo"],
          actor: "ops@example.com"
        })
      })
    );

    vi.unstubAllGlobals();
  });

  it("archives a workspace via the dedicated lifecycle endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        workspace_id: "workspace-1",
        customer_name: "Acme",
        project_name: "AdTech",
        environment: "dev",
        description: "",
        status: "archived",
        tags: []
      })
    });
    vi.stubGlobal("fetch", fetchMock);

    const archived = await createProductAPIClient(
      "http://api.example"
    ).archiveWorkspace("workspace-1", "ops@example.com");

    expect(archived.status).toBe("archived");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://api.example/api/workspaces/workspace-1/archive",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ actor: "ops@example.com" })
      })
    );

    vi.unstubAllGlobals();
  });

  it("raises a descriptive error when report export fails", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 400,
      statusText: "Bad Request",
      headers: { get: () => null },
      text: async () => "Unsupported report export format"
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      createProductAPIClient("http://api.example").exportReport(
        "report-1",
        "markdown"
      )
    ).rejects.toThrow(/Report export failed.*400.*Unsupported/);

    vi.unstubAllGlobals();
  });

  it("creates connection profiles through the product API without plaintext secrets", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        connection_profile_id: "connection-1",
        workspace_id: "workspace-1",
        name: "Development",
        deployment_mode: "local",
        endpoint: "http://localhost:8529",
        database: "customer_graph",
        username: "root",
        verify_ssl: false,
        secret_refs: { password: { kind: "env", ref: "ARANGO_PASSWORD" } }
      })
    });
    vi.stubGlobal("fetch", fetchMock);

    const profile = await createProductAPIClient(
      "http://api.example"
    ).createConnectionProfile("workspace-1", {
      name: "Development",
      deploymentMode: "local",
      endpoint: "http://localhost:8529",
      database: "customer_graph",
      username: "root",
      verifySsl: false,
      passwordSecretEnvVar: "ARANGO_PASSWORD"
    });

    expect(profile.connectionProfileId).toBe("connection-1");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://api.example/api/workspaces/workspace-1/connection-profiles",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          name: "Development",
          deployment_mode: "local",
          endpoint: "http://localhost:8529",
          database: "customer_graph",
          username: "root",
          verify_ssl: false,
          secret_refs: { password: { kind: "env", ref: "ARANGO_PASSWORD" } }
        })
      })
    );

    vi.unstubAllGlobals();
  });

  it("verifies connection profiles through the product API", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        connection_profile_id: "connection-1",
        workspace_id: "workspace-1",
        status: "success",
        verified_at: "2026-01-01T00:00:00Z",
        endpoint: "http://localhost:8529",
        database: "customer_graph",
        error_message: null
      })
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await createProductAPIClient(
      "http://api.example"
    ).verifyConnectionProfile("connection-1");

    expect(result).toMatchObject({
      connectionProfileId: "connection-1",
      status: "success",
      errorMessage: null
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://api.example/api/connection-profiles/connection-1/verify",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({})
      })
    );

    vi.unstubAllGlobals();
  });

  it("lists named graphs available on a connection profile", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        connection_profile_id: "connection-1",
        workspace_id: "workspace-1",
        database: "customer_graph",
        graphs: [
          {
            name: "CustomerGraph",
            is_system: false,
            vertex_collections: ["customers"],
            edge_collections: ["transactions"],
            orphan_collections: [],
            edge_definitions: [],
            vertex_count: 100,
            edge_count: 250
          }
        ]
      })
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await createProductAPIClient(
      "http://api.example"
    ).listConnectionProfileGraphs("connection-1");

    expect(result).toMatchObject({
      connectionProfileId: "connection-1",
      database: "customer_graph"
    });
    expect(result.graphs).toHaveLength(1);
    expect(result.graphs[0]).toMatchObject({
      name: "CustomerGraph",
      isSystem: false,
      vertexCount: 100,
      edgeCount: 250
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://api.example/api/connection-profiles/connection-1/graphs",
      expect.objectContaining({ method: "GET" })
    );

    vi.unstubAllGlobals();
  });

  it("discovers graph profiles through the product API", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        graph_profile: {
          graph_profile_id: "graph-profile-1",
          workspace_id: "workspace-1",
          connection_profile_id: "connection-1",
          graph_name: "CustomerGraph",
          status: "active",
          vertex_collections: ["customers"],
          edge_collections: ["transactions"]
        },
        schema_summary: {
          database_name: "customer_graph"
        }
      })
    });
    vi.stubGlobal("fetch", fetchMock);

    const discovery = await createProductAPIClient(
      "http://api.example"
    ).discoverGraphProfile("connection-1", {
      graphName: "CustomerGraph",
      sampleSize: 50,
      maxSamplesPerCollection: 2,
      verifySystem: false
    });

    expect(discovery.graphProfile).toMatchObject({
      graphProfileId: "graph-profile-1",
      graphName: "CustomerGraph"
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://api.example/api/connection-profiles/connection-1/discover-graph",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          graph_name: "CustomerGraph",
          sample_size: 50,
          max_samples_per_collection: 2,
          verify_system: false
        })
      })
    );

    vi.unstubAllGlobals();
  });

  it("starts Requirements Copilot sessions from graph profiles", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        requirement_interview_id: "interview-1",
        workspace_id: "workspace-1",
        graph_profile_id: "graph-profile-1",
        status: "draft",
        domain: "Clinical trials",
        questions: [
          {
            id: "business_goal",
            text: "What business decision should CustomerGraph support?"
          }
        ],
        schema_observations: {
          graph_name: "CustomerGraph"
        }
      })
    });
    vi.stubGlobal("fetch", fetchMock);

    const interview = await createProductAPIClient(
      "http://api.example"
    ).startRequirementsCopilot("graph-profile-1", {
      domain: "Clinical trials",
      createdBy: "analyst@example.com"
    });

    expect(interview).toMatchObject({
      requirementInterviewId: "interview-1",
      graphProfileId: "graph-profile-1",
      domain: "Clinical trials"
    });
    expect(interview.questions[0]).toMatchObject({ id: "business_goal" });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://api.example/api/graph-profiles/graph-profile-1/requirements-copilot/sessions",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          domain: "Clinical trials",
          created_by: "analyst@example.com"
        })
      })
    );

    vi.unstubAllGlobals();
  });

  it("answers Requirements Copilot questions through the product API", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        requirement_interview_id: "interview-1",
        workspace_id: "workspace-1",
        graph_profile_id: "graph-profile-1",
        status: "draft",
        answers: [
          {
            question_id: "business_goal",
            answer: "Prioritize trial sites"
          }
        ]
      })
    });
    vi.stubGlobal("fetch", fetchMock);

    const interview = await createProductAPIClient(
      "http://api.example"
    ).answerRequirementsCopilotQuestion(
      "interview-1",
      "business_goal",
      "Prioritize trial sites",
      "analyst@example.com"
    );

    expect(interview.answers[0]).toMatchObject({
      question_id: "business_goal",
      answer: "Prioritize trial sites"
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://api.example/api/requirements-copilot/sessions/interview-1/answer",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          question_id: "business_goal",
          answer: "Prioritize trial sites",
          actor: "analyst@example.com"
        })
      })
    );

    vi.unstubAllGlobals();
  });

  it("generates Requirements Copilot drafts through the product API", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        requirement_interview: {
          requirement_interview_id: "interview-1",
          workspace_id: "workspace-1",
          graph_profile_id: "graph-profile-1",
          status: "ready_for_review",
          draft_brd: "# Draft"
        },
        draft_brd: "# Draft",
        provenance_labels: [{ path: "answers", label: "user_provided" }]
      })
    });
    vi.stubGlobal("fetch", fetchMock);

    const draft = await createProductAPIClient(
      "http://api.example"
    ).generateRequirementsCopilotDraft("interview-1");

    expect(draft.requirementInterview.status).toBe("ready_for_review");
    expect(draft.draftBrd).toBe("# Draft");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://api.example/api/requirements-copilot/sessions/interview-1/generate-draft",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({})
      })
    );

    vi.unstubAllGlobals();
  });

  it("approves Requirements Copilot drafts through the product API", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        requirement_version_id: "requirement-version-1",
        workspace_id: "workspace-1",
        requirement_interview_id: "interview-1",
        version: 3,
        status: "approved",
        summary: "Prioritize trial sites",
        objectives: [{ id: "OBJ-1", text: "Prioritize trial sites" }]
      })
    });
    vi.stubGlobal("fetch", fetchMock);

    const requirementVersion = await createProductAPIClient(
      "http://api.example"
    ).approveRequirementsCopilotDraft("interview-1", 3, "analyst@example.com");

    expect(requirementVersion).toMatchObject({
      requirementVersionId: "requirement-version-1",
      version: 3,
      status: "approved",
      summary: "Prioritize trial sites"
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://api.example/api/requirements-copilot/sessions/interview-1/approve",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          approved_by: "analyst@example.com",
          version: 3
        })
      })
    );

    vi.unstubAllGlobals();
  });

  it("omits the version field when null is passed so the backend auto-increments", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        requirement_version_id: "requirement-version-7",
        workspace_id: "workspace-1",
        requirement_interview_id: "interview-1",
        version: 7,
        status: "approved",
        summary: "Auto-incremented"
      })
    });
    vi.stubGlobal("fetch", fetchMock);

    await createProductAPIClient("http://api.example").approveRequirementsCopilotDraft(
      "interview-1",
      null,
      "analyst@example.com"
    );

    expect(fetchMock).toHaveBeenCalledWith(
      "http://api.example/api/requirements-copilot/sessions/interview-1/approve",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ approved_by: "analyst@example.com" })
      })
    );

    vi.unstubAllGlobals();
  });

  it("forwards based_on_version_id when reopening a Requirements Copilot session", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        requirement_interview_id: "interview-2",
        workspace_id: "workspace-1",
        graph_profile_id: "graph-1",
        status: "draft",
        questions: [],
        answers: []
      })
    });
    vi.stubGlobal("fetch", fetchMock);

    await createProductAPIClient("http://api.example").startRequirementsCopilot(
      "graph-1",
      {
        domain: "AdTech",
        createdBy: "analyst@example.com",
        basedOnVersionId: "requirement-version-1"
      }
    );

    expect(fetchMock).toHaveBeenCalledWith(
      "http://api.example/api/graph-profiles/graph-1/requirements-copilot/sessions",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          domain: "AdTech",
          created_by: "analyst@example.com",
          based_on_version_id: "requirement-version-1"
        })
      })
    );

    vi.unstubAllGlobals();
  });

  it("exports workspace bundles through the product API", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        schema_version: "1",
        workspace: {
          workspace_id: "workspace-1",
          project_name: "Graph Analytics"
        },
        connection_profiles: [{ connection_profile_id: "connection-1" }],
        graph_profiles: [],
        source_documents: [],
        requirement_interviews: [],
        requirement_versions: [],
        workflow_runs: [],
        reports: [],
        audit_events: []
      })
    });
    vi.stubGlobal("fetch", fetchMock);

    const bundle = await createProductAPIClient("http://api.example").exportWorkspaceBundle(
      "workspace-1"
    );

    expect(bundle).toMatchObject({
      schemaVersion: "1",
      workspace: { workspace_id: "workspace-1" },
      connectionProfiles: [{ connection_profile_id: "connection-1" }]
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://api.example/api/workspaces/workspace-1/export",
      expect.objectContaining({ method: "GET" })
    );

    vi.unstubAllGlobals();
  });

  it("imports workspace bundles through the product API", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        workspace_id: "workspace-1",
        counts: {
          connection_profiles: 1,
          reports: 2
        }
      })
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await createProductAPIClient("http://api.example").importWorkspaceBundle({
      schemaVersion: "1",
      workspace: { workspace_id: "workspace-1" },
      connectionProfiles: [{ connection_profile_id: "connection-1" }],
      graphProfiles: [],
      sourceDocuments: [],
      requirementInterviews: [],
      requirementVersions: [],
      workflowRuns: [],
      reports: [],
      auditEvents: []
    });

    expect(result).toEqual({
      workspaceId: "workspace-1",
      counts: {
        connection_profiles: 1,
        reports: 2
      }
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://api.example/api/workspaces/import",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          schema_version: "1",
          workspace: { workspace_id: "workspace-1" },
          connection_profiles: [{ connection_profile_id: "connection-1" }],
          graph_profiles: [],
          source_documents: [],
          requirement_interviews: [],
          requirement_versions: [],
          workflow_runs: [],
          reports: [],
          audit_events: []
        })
      })
    );

    vi.unstubAllGlobals();
  });

  it("loads workflow recovery actions through the product API", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        "step-1": ["retry", "open_logs"],
        "step-2": []
      })
    });
    vi.stubGlobal("fetch", fetchMock);

    const actions = await createProductAPIClient(
      "http://api.example"
    ).getWorkflowRecoveryActions("run-1");

    expect(actions["step-1"]).toEqual(["retry", "open_logs"]);
    expect(fetchMock).toHaveBeenCalledWith(
      "http://api.example/api/runs/run-1/recovery-actions",
      expect.objectContaining({ method: "GET" })
    );

    vi.unstubAllGlobals();
  });

  it("starts workflow runs through the product API", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        run_id: "run-1",
        workspace_id: "workspace-1",
        workflow_mode: "agentic",
        status: "running",
        started_at: "2026-01-01T00:00:00Z"
      })
    });
    vi.stubGlobal("fetch", fetchMock);

    const workflowRun = await createProductAPIClient("http://api.example").startWorkflowRun(
      "run-1"
    );

    expect(workflowRun).toMatchObject({
      runId: "run-1",
      workflowMode: "agentic",
      status: "running"
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://api.example/api/runs/run-1/start",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({})
      })
    );

    vi.unstubAllGlobals();
  });

  it("updates workflow steps through the product API", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        workflow_run: {
          run_id: "run-1",
          workspace_id: "workspace-1",
          workflow_mode: "agentic",
          status: "running"
        },
        dag_view: {
          run_id: "run-1",
          workspace_id: "workspace-1",
          status: "running",
          workflow_mode: "agentic",
          nodes: [
            {
              id: "step-1",
              label: "Retry",
              status: "running",
              artifact_refs: [],
              warnings: [],
              errors: []
            }
          ],
          edges: [],
          warnings: [],
          errors: []
        }
      })
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await createProductAPIClient("http://api.example").updateWorkflowStep(
      "run-1",
      "step-1",
      "running"
    );

    expect(result.dagView.nodes[0].status).toBe("running");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://api.example/api/runs/run-1/steps/step-1",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({ status: "running" })
      })
    );

    vi.unstubAllGlobals();
  });

  it("creates workflow runs from planned steps through the product API", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        run_id: "run-1",
        workspace_id: "workspace-1",
        workflow_mode: "agentic",
        status: "queued",
        steps: [
          {
            step_id: "schema-discovery",
            label: "Schema Discovery",
            status: "pending"
          },
          {
            step_id: "agent-analysis",
            label: "Agent Analysis",
            status: "pending"
          }
        ],
        dag_edges: [
          {
            from_step_id: "schema-discovery",
            to_step_id: "agent-analysis"
          }
        ]
      })
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await createProductAPIClient("http://api.example").createWorkflowRun(
      "workspace-1",
      {
        workflowMode: "agentic",
        stepLabels: ["Schema Discovery", "Agent Analysis"]
      }
    );

    expect(result.workflowRun.status).toBe("queued");
    expect(result.dagView.nodes.map((node) => node.id)).toEqual([
      "schema-discovery",
      "agent-analysis"
    ]);
    expect(fetchMock).toHaveBeenCalledWith(
      "http://api.example/api/runs",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          workspace_id: "workspace-1",
          workflow_mode: "agentic",
          steps: [
            {
              step_id: "schema-discovery",
              label: "Schema Discovery",
              status: "pending"
            },
            {
              step_id: "agent-analysis",
              label: "Agent Analysis",
              status: "pending"
            }
          ],
          dag_edges: [
            {
              from_step_id: "schema-discovery",
              to_step_id: "agent-analysis"
            }
          ]
        })
      })
    );

    vi.unstubAllGlobals();
  });

  it("posts cancel requests to /api/runs/{id}/cancel with optional actor", async () => {
    // FR-31a: cooperative cancel. The client passes the actor through
    // so the backend can attribute the audit event; when no actor is
    // passed the body is empty so the backend uses its default.
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        run_id: "run-1",
        workspace_id: "workspace-1",
        workflow_mode: "agentic",
        status: "running",
        started_at: "2026-05-06T20:00:00Z",
        completed_at: null
      })
    });
    vi.stubGlobal("fetch", fetchMock);

    const summary = await createProductAPIClient(
      "http://api.example"
    ).cancelWorkflowRun("run-1", "qa@example.com");

    expect(summary).toMatchObject({
      runId: "run-1",
      status: "running",
      workflowMode: "agentic"
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://api.example/api/runs/run-1/cancel",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ actor: "qa@example.com" })
      })
    );

    vi.unstubAllGlobals();
  });

  it("omits actor from the cancel body when caller doesn't pass one", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        run_id: "run-1",
        workspace_id: "workspace-1",
        workflow_mode: "agentic",
        status: "cancelled",
        started_at: "2026-05-06T20:00:00Z",
        completed_at: "2026-05-06T20:01:00Z"
      })
    });
    vi.stubGlobal("fetch", fetchMock);

    await createProductAPIClient("http://api.example").cancelWorkflowRun("run-1");

    expect(fetchMock).toHaveBeenCalledWith(
      "http://api.example/api/runs/run-1/cancel",
      expect.objectContaining({ method: "POST", body: JSON.stringify({}) })
    );

    vi.unstubAllGlobals();
  });

  it("maps the FR-31a status snapshot from snake_case to camelCase", async () => {
    // Pins the supervisor-side fields the UI's status panel will rely
    // on (executor_kind so we can label inprocess Phase 1 rows
    // distinctly later, last_outcome so we can show "cancelled" the
    // moment the supervisor finalizes the run).
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        run_id: "run-1",
        workspace_id: "workspace-1",
        workflow_mode: "agentic",
        status: "running",
        started_at: "2026-05-06T20:00:00Z",
        completed_at: null,
        executor_kind: "inprocess",
        last_outcome: "running",
        errors: [],
        supervisor: {
          supervised: true,
          outcome: "running",
          cancel_requested: false
        }
      })
    });
    vi.stubGlobal("fetch", fetchMock);

    const status = await createProductAPIClient(
      "http://api.example"
    ).getWorkflowRunStatus("run-1");

    expect(status).toEqual({
      runId: "run-1",
      workspaceId: "workspace-1",
      workflowMode: "agentic",
      status: "running",
      startedAt: "2026-05-06T20:00:00Z",
      completedAt: null,
      executorKind: "inprocess",
      lastOutcome: "running",
      errors: [],
      supervisor: {
        supervised: true,
        outcome: "running",
        cancelRequested: false
      }
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://api.example/api/runs/run-1/status",
      expect.objectContaining({ method: "GET" })
    );

    vi.unstubAllGlobals();
  });

  it("maps use case payloads and posts status changes (FR-19/FR-20)", async () => {
    // Fixture mirrors UseCase.to_dict, captured by running the service.
    const rawUseCase = {
      _key: "use-case-1",
      use_case_id: "use-case-1",
      workspace_id: "workspace-1",
      title: "Find fraud rings",
      description: "Detect rings across linked accounts",
      use_case_type: "community",
      priority: "high",
      status: "draft",
      origin: "manual",
      requirement_version_id: null,
      related_requirements: [],
      graph_algorithms: ["wcc"],
      data_needs: ["Account"],
      expected_outputs: ["ring clusters"],
      success_metrics: ["precision > 0.8"],
      reviewed_by: null,
      reviewed_at: null,
      review_note: "",
      created_at: "2026-08-14T00:00:00+00:00",
      updated_at: "2026-08-14T00:00:00+00:00",
      created_by: "analyst@example.com",
      metadata: {}
    };
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ ...rawUseCase, status: "approved" })
    });
    vi.stubGlobal("fetch", fetchMock);

    const useCase = await createProductAPIClient(
      "http://api.example"
    ).setUseCaseStatus("use-case-1", "approved", "Looks good");

    expect(useCase).toMatchObject({
      useCaseId: "use-case-1",
      title: "Find fraud rings",
      status: "approved",
      origin: "manual",
      graphAlgorithms: ["wcc"]
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://api.example/api/use-cases/use-case-1/status",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ status: "approved", review_note: "Looks good" })
      })
    );

    vi.unstubAllGlobals();
  });

  it("maps analysis template payloads including version lineage (FR-23/FR-25)", async () => {
    // Fixture mirrors AnalysisTemplate.to_dict. Editing an approved template
    // returns a DIFFERENT row (the next version) sharing the lineage id — the
    // mapper must carry both ids through or the UI would show the stale row.
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        _key: "analysis-template-2",
        analysis_template_id: "analysis-template-2",
        workspace_id: "workspace-1",
        name: "PageRank on Person",
        lineage_id: "analysis-template-1",
        description: "Rank people by influence",
        algorithm: "pagerank",
        parameters: { damping_factor: 0.5 },
        config: { graph_name: "kg" },
        version: 2,
        status: "draft",
        use_case_id: "use-case-1",
        estimated_runtime_seconds: 45.0,
        superseded_by: null,
        approved_by: null,
        approved_at: null,
        created_at: "2026-08-14T00:00:00+00:00",
        updated_at: "2026-08-14T00:00:00+00:00",
        created_by: null,
        metadata: { supersedes: "analysis-template-1" }
      })
    });
    vi.stubGlobal("fetch", fetchMock);

    const template = await createProductAPIClient(
      "http://api.example"
    ).updateAnalysisTemplate("analysis-template-1", {
      parameters: { damping_factor: 0.5 }
    });

    expect(template).toMatchObject({
      analysisTemplateId: "analysis-template-2",
      lineageId: "analysis-template-1",
      version: 2,
      status: "draft",
      parameters: { damping_factor: 0.5 }
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://api.example/api/analysis-templates/analysis-template-1",
      expect.objectContaining({ method: "PATCH" })
    );

    vi.unstubAllGlobals();
  });

  it("posts template dictionaries to the import route (FR-26)", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => [] });
    vi.stubGlobal("fetch", fetchMock);

    await createProductAPIClient("http://api.example").importAnalysisTemplates(
      "workspace-1",
      [{ name: "WCC", algorithm: "wcc", cmd: "rm -rf /" }]
    );

    // The client forwards the payload verbatim; the *server* is what decides
    // which keys are read, so hostile keys never need special-casing here.
    expect(fetchMock).toHaveBeenCalledWith(
      "http://api.example/api/workspaces/workspace-1/analysis-templates/import",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          templates: [{ name: "WCC", algorithm: "wcc", cmd: "rm -rf /" }]
        })
      })
    );

    vi.unstubAllGlobals();
  });

  it("maps the analysis catalog browse payload (FR-45)", async () => {
    // Fixture mirrors the exact dicts ProductService.browse_analysis_catalog /
    // AnalysisExecution.to_dict / AnalysisEpoch.to_dict return — captured by
    // running the Python service, not inferred from the TypeScript types, so
    // this cannot pass while the wire format drifts.
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        workspace_id: "workspace-1",
        epochs: [
          {
            _key: "analysis-epoch-1",
            analysis_epoch_id: "analysis-epoch-1",
            workspace_id: "workspace-1",
            name: "2026-Q3",
            description: "Q3 snapshot",
            timestamp: "2026-07-01T00:00:00+00:00",
            status: "active",
            tags: ["quarterly"],
            parent_epoch_id: null,
            analysis_count: 1,
            analysis_execution_ids: ["analysis-execution-1"],
            catalog_epoch_id: null,
            created_at: "2026-07-01T00:00:00+00:00",
            updated_at: "2026-07-01T00:00:00+00:00",
            metadata: {}
          }
        ],
        executions: [
          {
            _key: "analysis-execution-1",
            analysis_execution_id: "analysis-execution-1",
            workspace_id: "workspace-1",
            run_id: "run-1",
            algorithm: "pagerank",
            status: "completed",
            graph_profile_id: null,
            requirement_version_id: null,
            use_case_id: "uc-1",
            template_id: "tpl-1",
            template_name: "PageRank on Person",
            epoch_id: "analysis-epoch-1",
            algorithm_version: "",
            parameters: {},
            graph_config: {},
            results_location: "pagerank_results",
            result_count: 1200,
            performance_metrics: { duration_ms: 4200 },
            result_sample: null,
            error_message: null,
            workflow_mode: "agentic",
            catalog_execution_id: null,
            started_at: "2026-07-01T09:00:00+00:00",
            completed_at: null,
            created_at: "2026-07-01T09:00:00+00:00",
            updated_at: "2026-07-01T09:00:00+00:00",
            metadata: {}
          }
        ],
        templates: [{ template_id: "tpl-1" }],
        use_cases: [],
        requirements: []
      })
    });
    vi.stubGlobal("fetch", fetchMock);

    const view =
      await createProductAPIClient("http://api.example").browseAnalysisCatalog(
        "workspace-1"
      );

    expect(view.workspaceId).toBe("workspace-1");
    expect(view.epochs[0]).toMatchObject({
      analysisEpochId: "analysis-epoch-1",
      name: "2026-Q3",
      analysisCount: 1
    });
    expect(view.executions[0]).toMatchObject({
      analysisExecutionId: "analysis-execution-1",
      algorithm: "pagerank",
      status: "completed",
      templateName: "PageRank on Person",
      resultCount: 1200,
      performanceMetrics: { duration_ms: 4200 }
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://api.example/api/workspaces/workspace-1/analysis-catalog",
      expect.objectContaining({ method: "GET" })
    );

    vi.unstubAllGlobals();
  });

  it("sends only the filters that are set when searching executions (FR-46)", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => [] });
    vi.stubGlobal("fetch", fetchMock);

    await createProductAPIClient("http://api.example").listAnalysisExecutions(
      "workspace-1",
      { algorithm: "pagerank", status: undefined, epochId: "" }
    );

    // A present-but-empty filter would be a real constraint server-side and
    // would match nothing, so blank values must never be sent.
    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe(
      "http://api.example/api/workspaces/workspace-1/analysis-executions?algorithm=pagerank"
    );
    expect(url).not.toContain("status=");
    expect(url).not.toContain("epoch_id=");

    vi.unstubAllGlobals();
  });

  it("maps execution comparison deltas (FR-48)", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        workspace_id: "workspace-1",
        baseline_execution_id: "analysis-execution-1",
        executions: [],
        deltas: [
          {
            analysis_execution_id: "analysis-execution-1",
            result_count: 0,
            performance_metrics: { duration_ms: 0 }
          },
          {
            analysis_execution_id: "analysis-execution-2",
            result_count: -400,
            performance_metrics: { duration_ms: -2100 }
          }
        ]
      })
    });
    vi.stubGlobal("fetch", fetchMock);

    const comparison = await createProductAPIClient(
      "http://api.example"
    ).compareAnalysisExecutions("workspace-1", [
      "analysis-execution-1",
      "analysis-execution-2"
    ]);

    expect(comparison.baselineExecutionId).toBe("analysis-execution-1");
    expect(comparison.deltas[1]).toEqual({
      analysisExecutionId: "analysis-execution-2",
      resultCount: -400,
      performanceMetrics: { duration_ms: -2100 }
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://api.example/api/workspaces/workspace-1/analysis-executions/compare",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          analysis_execution_ids: ["analysis-execution-1", "analysis-execution-2"]
        })
      })
    );

    vi.unstubAllGlobals();
  });

  it("maps execution lineage (FR-47)", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        workspace_id: "workspace-1",
        reports: [],
        execution: {
          analysis_execution_id: "analysis-execution-1",
          workspace_id: "workspace-1",
          algorithm: "pagerank",
          status: "completed"
        },
        template_id: "tpl-1",
        use_case_id: "uc-1",
        requirement_version_id: "rv-1"
      })
    });
    vi.stubGlobal("fetch", fetchMock);

    const lineage = await createProductAPIClient(
      "http://api.example"
    ).getAnalysisLineage("analysis-execution-1");

    expect(lineage.execution?.algorithm).toBe("pagerank");
    // Fields the backend omits must default rather than land as undefined.
    expect(lineage.execution?.resultCount).toBe(0);
    expect(lineage.execution?.templateName).toBe("");
    expect(lineage).toMatchObject({
      templateId: "tpl-1",
      useCaseId: "uc-1",
      requirementVersionId: "rv-1"
    });

    vi.unstubAllGlobals();
  });

  it("uploads source documents through the product API (FR-13)", async () => {
    // Fixture mirrors the exact dict ProductService.upload_source_document
    // returns (verified against the Python service, not assumed from the
    // TypeScript types) so this cannot pass while the wire format drifts.
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        _key: "document-1",
        document_id: "document-1",
        workspace_id: "workspace-1",
        filename: "requirements.md",
        mime_type: "text/markdown",
        sha256: "19812277b04a5a988e4dc361617bcb927d4297c47353da93aa992ac007f1f3cf",
        storage_mode: "extract_only",
        storage_uri: null,
        extracted_text: "# Hi\n",
        uploaded_at: "2026-08-02T04:40:49.228704+00:00",
        metadata: { byte_size: 5, uploaded_by: "analyst@example.com" }
      })
    });
    vi.stubGlobal("fetch", fetchMock);

    const document = await createProductAPIClient(
      "http://api.example"
    ).uploadSourceDocument("workspace-1", {
      filename: "requirements.md",
      mimeType: "text/markdown",
      contentBase64: "IyBIaQo="
    });

    expect(document).toMatchObject({
      documentId: "document-1",
      filename: "requirements.md",
      storageMode: "extract_only",
      extractedText: "# Hi\n"
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://api.example/api/workspaces/workspace-1/documents",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          filename: "requirements.md",
          mime_type: "text/markdown",
          content_base64: "IyBIaQo="
        })
      })
    );

    vi.unstubAllGlobals();
  });
});

describe("resolveArtifactAsset", () => {
  const assets: WorkspaceAsset[] = [
    { id: "report-1", kind: "report", label: "Report One", description: "" },
    { id: "graph-profile-1", kind: "graph-profile", label: "G", description: "" },
    { id: "requirements:workspace-1", kind: "requirements", label: "Requirements", description: "" },
    { id: "use-cases:workspace-1", kind: "use-cases", label: "Use Cases", description: "" },
    { id: "analysis-catalog:workspace-1", kind: "analysis-catalog", label: "Catalog", description: "" }
  ];

  it("routes per-record refs by id", () => {
    expect(resolveArtifactAsset({ type: "report", id: "report-1" }, assets)?.kind).toBe("report");
    expect(
      resolveArtifactAsset({ type: "graph_profile", id: "graph-profile-1" }, assets)?.kind
    ).toBe("graph-profile");
  });

  it("routes singleton refs by kind, since their asset ids are workspace-scoped", () => {
    // The ref id is a requirement-version id, which never equals the
    // synthetic `requirements:<workspace>` asset id — matching on id here
    // would silently fail to resolve.
    expect(
      resolveArtifactAsset({ type: "requirement_version", id: "requirement-version-9" }, assets)?.kind
    ).toBe("requirements");
    expect(
      resolveArtifactAsset({ type: "analysis_template", id: "template-9" }, assets)?.kind
    ).toBe("use-cases");
    expect(
      resolveArtifactAsset({ type: "analysis_execution", id: "execution-9" }, assets)?.kind
    ).toBe("analysis-catalog");
  });

  it("returns null when nothing can show the ref, so it stays plain text", () => {
    expect(resolveArtifactAsset({ type: "report", id: "missing" }, assets)).toBeNull();
    expect(resolveArtifactAsset({ type: "unknown_kind", id: "x" }, assets)).toBeNull();
  });
});
