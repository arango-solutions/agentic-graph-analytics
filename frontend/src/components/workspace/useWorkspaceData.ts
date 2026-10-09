"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  connectionSecretRefs,
  createProductAPIClient,
  workspaceAssetsFromOverview
} from "@/lib/product-api/client";
import {
  demoAssets,
  demoConnectionProfile,
  demoDag,
  demoGraphProfile,
  demoReport,
  demoSourceDocument
} from "@/lib/product-api/demoData";
import type {
  ClusterDatabasesResult,
  DefaultClusterDatabasesResult,
  ConnectionDefaults,
  ConnectionGraphsResult,
  ConnectionProfileSummary,
  ConnectionVerificationResult,
  CreateConnectionProfileInput,
  ListClusterDatabasesInput,
  CreateWorkspaceInput,
  CreateWorkflowRunInput,
  CreateWorkflowRunResult,
  DiscoverGraphProfileInput,
  GraphDiscoveryResult,
  GraphProfileSummary,
  ProductAPIClient,
  QuickAnalysisInput,
  RequirementInterview,
  RequirementVersion,
  RequirementsDraftResult,
  ReportBundle,
  ReportExportDownload,
  ReportExportFormat,
  SourceDocumentSummary,
  AnalysisCatalogView,
  AnalysisExecution,
  AnalysisTemplate,
  AnalysisExecutionComparison,
  AnalysisExecutionFilters,
  AnalysisLineage,
  CreateAnalysisTemplateInput,
  CreateUseCaseInput,
  UseCase,
  UseCaseStatus,
  RetentionPolicy,
  RetentionSweepResult,
  SetRetentionPolicyInput,
  VerticalProjectImportResult,
  StartRequirementsCopilotInput,
  UpdateWorkspaceInput,
  UploadSourceDocumentInput,
  WorkflowDAGView,
  WorkflowRecoveryActions,
  WorkflowRunStatusView,
  WorkflowRunSummary,
  WorkflowStepStatus,
  WorkflowStepUpdateResult,
  WorkspaceAsset,
  WorkspaceBundle,
  WorkspaceHealth,
  WorkspaceImportResult,
  WorkspaceOverview,
  WorkspaceSummary
} from "@/lib/product-api/types";

let demoRequirementInterview: RequirementInterview | null = null;

interface UseWorkspaceDataArgs {
  initialWorkspaceId?: string;
  initialRunId?: string;
  client?: ProductAPIClient;
}

interface WorkspaceDataState {
  assets: WorkspaceAsset[];
  connectionProfileById: Record<string, ConnectionProfileSummary>;
  graphProfileById: Record<string, GraphProfileSummary>;
  documentById: Record<string, SourceDocumentSummary>;
  dagByRunId: Record<string, WorkflowDAGView>;
  recoveryActionsByRunId: Record<string, WorkflowRecoveryActions>;
  reportById: Record<string, ReportBundle>;
  overview: WorkspaceOverview | null;
  health: WorkspaceHealth | null;
  status: "demo" | "loading" | "ready" | "error";
  errorMessage?: string;
}

interface WorkspaceDataResult extends WorkspaceDataState {
  createWorkspace: (input: CreateWorkspaceInput) => Promise<WorkspaceSummary>;
  /** Patch editable workspace metadata. Calls refreshOverview() on success
   * so the rest of the UI sees the new values without a manual reload. */
  updateWorkspace: (
    workspaceId: string,
    input: UpdateWorkspaceInput
  ) => Promise<WorkspaceSummary>;
  /** Soft-delete (archive) a workspace. Lifecycle change emits a typed
   * audit event server-side; the local overview is refreshed so the UI
   * can disable mutating actions on the now-archived workspace. */
  archiveWorkspace: (workspaceId: string, actor?: string) => Promise<WorkspaceSummary>;
  /** FR-67b: set (or clear) which GraphProfile drives the workbench's
   * "Analyzing X" banner and the default Requirements Copilot target.
   * Pass null to clear and fall back to the deterministic positional
   * rule. Triggers a refreshOverview() so the rest of the UI sees the
   * change in one round trip. */
  setActiveGraphProfile: (
    workspaceId: string,
    graphProfileId: string | null,
    actor?: string
  ) => Promise<WorkspaceSummary>;
  publishReport: (reportId: string, actor?: string) => Promise<ReportBundle>;
  /** Download a rendered report as a Blob (HTML or Markdown). The caller is
   * responsible for triggering the browser download (e.g. via
   * createObjectURL + an anchor element). In demo mode this returns a
   * placeholder Blob so the UI affordance still works. */
  exportReport: (
    reportId: string,
    format: ReportExportFormat
  ) => Promise<ReportExportDownload>;
  createConnectionProfile: (
    input: CreateConnectionProfileInput
  ) => Promise<ConnectionProfileSummary>;
  verifyConnectionProfile: (connectionProfileId: string) => Promise<ConnectionVerificationResult>;
  /** Two-step connect, part 1: list databases visible to cluster creds. */
  listDefaultClusterDatabases: () => Promise<DefaultClusterDatabasesResult>;
  deleteConnectionProfile: (connectionProfileId: string) => Promise<void>;
  deleteGraphProfile: (graphProfileId: string) => Promise<void>;
  deleteWorkflowRun: (runId: string) => Promise<void>;
  listClusterDatabases: (
    input: ListClusterDatabasesInput
  ) => Promise<ClusterDatabasesResult>;
  /** Non-secret connection defaults from the environment, for form prefill. */
  getConnectionDefaults: () => Promise<ConnectionDefaults>;
  /** FR-13: upload a source document into the active workspace. */
  uploadSourceDocument: (
    input: UploadSourceDocumentInput
  ) => Promise<SourceDocumentSummary>;
  /** FR-19: author a use case. */
  createUseCase: (input: CreateUseCaseInput) => Promise<UseCase>;
  /** FR-20: approve / reject / archive a use case. */
  setUseCaseStatus: (
    useCaseId: string,
    status: UseCaseStatus,
    reviewNote?: string
  ) => Promise<UseCase>;
  /** FR-20: re-prioritise a use case. */
  setUseCasePriority: (useCaseId: string, priority: string) => Promise<UseCase>;
  /** FR-22: create a draft analysis template. */
  createAnalysisTemplate: (
    input: CreateAnalysisTemplateInput
  ) => Promise<AnalysisTemplate>;
  /** FR-23/FR-25: edit parameters; versions an approved template. */
  updateAnalysisTemplate: (
    analysisTemplateId: string,
    patch: { parameters?: Record<string, unknown>; config?: Record<string, unknown> }
  ) => Promise<AnalysisTemplate>;
  /** FR-25: approve a draft template. */
  approveAnalysisTemplate: (analysisTemplateId: string) => Promise<AnalysisTemplate>;
  /** FR-25: version history for a template lineage. */
  getAnalysisTemplateVersions: (
    analysisTemplateId: string
  ) => Promise<AnalysisTemplate[]>;
  /** FR-26: import template dictionaries. */
  importAnalysisTemplates: (
    templates: Array<Record<string, unknown>>
  ) => Promise<AnalysisTemplate[]>;
  /** FR-54: read the workspace retention policy. */
  getRetentionPolicy: () => Promise<RetentionPolicy>;
  /** FR-54: configure retention windows. */
  setRetentionPolicy: (input: SetRetentionPolicyInput) => Promise<RetentionPolicy>;
  /** FR-54: sweep expired records; dry run unless dryRun is false. */
  applyRetentionPolicy: (dryRun?: boolean) => Promise<RetentionSweepResult>;
  /** FR-49/FR-50: import a vertical project bundle. */
  importVerticalProject: (
    document: string,
    documentFormat?: string
  ) => Promise<VerticalProjectImportResult>;
  /** FR-45: browse the workspace's analysis epochs and executions. */
  browseAnalysisCatalog: () => Promise<AnalysisCatalogView>;
  /** FR-46: server-side filtered execution search. */
  listAnalysisExecutions: (
    filters?: AnalysisExecutionFilters
  ) => Promise<AnalysisExecution[]>;
  /** FR-48: compare executions; deltas are relative to the first id. */
  compareAnalysisExecutions: (
    analysisExecutionIds: string[]
  ) => Promise<AnalysisExecutionComparison>;
  /** FR-47: trace an execution back to its requirement. */
  getAnalysisLineage: (analysisExecutionId: string) => Promise<AnalysisLineage>;
  listConnectionProfileGraphs: (
    connectionProfileId: string
  ) => Promise<ConnectionGraphsResult>;
  discoverGraphProfile: (
    connectionProfileId: string,
    input: DiscoverGraphProfileInput
  ) => Promise<GraphDiscoveryResult>;
  startRequirementsCopilot: (
    graphProfileId: string,
    input: StartRequirementsCopilotInput
  ) => Promise<RequirementInterview>;
  answerRequirementsCopilotQuestion: (
    requirementInterviewId: string,
    questionId: string,
    answer: string,
    actor?: string
  ) => Promise<RequirementInterview>;
  generateRequirementsCopilotDraft: (
    requirementInterviewId: string
  ) => Promise<RequirementsDraftResult>;
  approveRequirementsCopilotDraft: (
    requirementInterviewId: string,
    version: number | null,
    approvedBy?: string
  ) => Promise<RequirementVersion>;
  /** Snapshot of the active (most recent APPROVED) requirement version, if any. */
  approvedRequirementVersion: RequirementVersion | null;
  /** All requirement versions known to the workspace (any status). */
  requirementVersions: RequirementVersion[];
  /** Re-fetch the workspace overview (assets, latest versions, audit, etc.)
   * after a mutation. Cheaper than a full reload — does NOT re-fetch run
   * DAGs or report bundles, just the projection that drives the AssetExplorer
   * + RequirementVersionCanvas. Returns a no-op promise when the hook is in
   * demo mode. */
  refreshOverview: () => Promise<void>;
  /** FR-15: promote a document's extracted requirements into a DRAFT
   * requirement version, then refresh so the new version is selectable. */
  promoteExtractedRequirements: (documentId: string) => Promise<RequirementVersion>;
  exportWorkspaceBundle: () => Promise<WorkspaceBundle>;
  importWorkspaceBundle: (bundle: WorkspaceBundle) => Promise<WorkspaceImportResult>;
  createWorkflowRun: (input: CreateWorkflowRunInput) => Promise<CreateWorkflowRunResult>;
  /** FR-73: one-shot analysis from a prompt against a graph profile. */
  quickAnalysis: (input: QuickAnalysisInput) => Promise<CreateWorkflowRunResult>;
  startWorkflowRun: (runId: string) => Promise<WorkflowRunSummary>;
  /** FR-31a: cooperative cancel for an agentic run. */
  cancelWorkflowRun: (runId: string, actor?: string) => Promise<WorkflowRunSummary>;
  /** FR-31a: lightweight status poll. Returns null in demo mode. */
  getWorkflowRunStatus: (runId: string) => Promise<WorkflowRunStatusView | null>;
  updateWorkflowStep: (
    runId: string,
    stepId: string,
    status: WorkflowStepStatus
  ) => Promise<WorkflowStepUpdateResult>;
}

export function useWorkspaceData({
  initialWorkspaceId,
  initialRunId,
  client
}: UseWorkspaceDataArgs): WorkspaceDataResult {
  const apiClient = useMemo(() => client ?? createProductAPIClient(), [client]);
  const [state, setState] = useState<WorkspaceDataState>({
    assets: demoAssets,
    connectionProfileById: {
      [demoConnectionProfile.connectionProfileId]: demoConnectionProfile
    },
    graphProfileById: { [demoGraphProfile.graphProfileId]: demoGraphProfile },
    documentById: { [demoSourceDocument.documentId]: demoSourceDocument },
    dagByRunId: { [demoDag.runId]: demoDag },
    recoveryActionsByRunId: { [demoDag.runId]: demoRecoveryActions(demoDag) },
    reportById: { [demoReport.manifest.reportId]: demoReport },
    overview: null,
    health: null,
    status: "demo"
  });

  // Bumping this re-runs the loader. Used by the auto-retry below and by any
  // caller that wants a full reload rather than refreshOverview()'s partial one.
  const [reloadNonce, setReloadNonce] = useState(0);
  const retryAttemptRef = useRef(0);

  useEffect(() => {
    let cancelled = false;
    setState((current) => ({ ...current, status: "loading", errorMessage: undefined }));

    async function resolveWorkspaceId(): Promise<string | null> {
      if (initialWorkspaceId) {
        return initialWorkspaceId;
      }
      try {
        const workspaces = await apiClient.listWorkspaces();
        if (workspaces.length === 0) {
          return null;
        }
        return workspaces[0].workspaceId;
      } catch {
        return null;
      }
    }

    async function loadWorkspace() {
      const workspaceId = await resolveWorkspaceId();
      if (cancelled) {
        return;
      }
      if (!workspaceId) {
        // No real workspaces in the Product API yet — fall back to the canned
        // demo data the initial state was seeded with.
        setState((current) => ({ ...current, status: "demo", errorMessage: undefined }));
        return;
      }

      try {
        const [overview, health] = await Promise.all([
          apiClient.getWorkspaceOverview(workspaceId),
          apiClient.getWorkspaceHealth(workspaceId)
        ]);
        const assets = workspaceAssetsFromOverview(overview);
        const firstRunId =
          initialRunId ??
          assets.find((asset) => asset.kind === "run")?.id;
        const [dag, recoveryActions] = firstRunId
          ? await Promise.all([
              apiClient.getWorkflowDAG(firstRunId),
              apiClient.getWorkflowRecoveryActions(firstRunId)
            ])
          : [null, null];
        const reportBundles = await Promise.all(
          assets
            .filter((asset) => asset.kind === "report")
            .map((asset) => apiClient.getReportBundle(asset.id))
        );
        const reportById = Object.fromEntries(
          reportBundles.map((report) => [report.manifest.reportId, report])
        );

        if (cancelled) {
          return;
        }

        // A real workspace shows its own contents, empty or not. Substituting
        // demo fixtures when a section came back empty made a brand-new
        // workspace look populated with a "Demo ArangoDB" connection nobody
        // created — indistinguishable at a glance from real records.
        setState({
          assets,
          connectionProfileById: Object.fromEntries(
            overview.latestConnectionProfiles.map((profile) => [
              profile.connectionProfileId,
              profile
            ])
          ),
          graphProfileById: Object.fromEntries(
            overview.latestGraphProfiles.map((profile) => [
              profile.graphProfileId,
              profile
            ])
          ),
          documentById: Object.fromEntries(
            overview.latestSourceDocuments.map((document) => [
              document.documentId,
              document
            ])
          ),
          dagByRunId: dag ? { [dag.runId]: dag } : {},
          recoveryActionsByRunId:
            firstRunId && recoveryActions ? { [firstRunId]: recoveryActions } : {},
          reportById,
          overview,
          health,
          status: "ready"
        });
      } catch (error) {
        if (cancelled) {
          return;
        }

        // Clear the seeded demo fixtures. Keeping them meant a failed load
        // presented a different, fake workspace as though it were the one
        // requested — a live connection profile looked deleted when the API
        // was merely restarting.
        setState({
          assets: [],
          connectionProfileById: {},
          graphProfileById: {},
          documentById: {},
          dagByRunId: {},
          recoveryActionsByRunId: {},
          reportById: {},
          overview: null,
          health: null,
          status: "error",
          errorMessage:
            error instanceof Error ? error.message : "Failed to load workspace"
        });
      }
    }

    void loadWorkspace();

    return () => {
      cancelled = true;
    };
  }, [apiClient, initialRunId, initialWorkspaceId, reloadNonce]);

  // Recover from a transient backend outage without the user noticing. A brief
  // API restart previously left the workspace stuck in the error state
  // indefinitely — it never re-fetched, so the only way back was a manual
  // reload, and it was easy to mistake the empty panel for lost data.
  // Backoff is exponential from 2s, capped at 30s. `reloadNonce` is a
  // dependency because a failed retry leaves status on "error", which would
  // not by itself re-trigger this effect and would end the retry chain.
  useEffect(() => {
    if (state.status !== "error") {
      retryAttemptRef.current = 0;
      return;
    }
    const attempt = retryAttemptRef.current;
    const delayMs = Math.min(2000 * 2 ** attempt, 30000);
    const timeoutId = setTimeout(() => {
      retryAttemptRef.current = attempt + 1;
      setReloadNonce((current) => current + 1);
    }, delayMs);
    return () => clearTimeout(timeoutId);
  }, [state.status, reloadNonce]);

  // CRITICAL: `initialWorkspaceId` alone is NOT a sufficient signal for "use
  // the real API". The loader has a fallback (lines 142-150) that discovers a
  // workspace via `apiClient.listWorkspaces()` when the URL has no
  // `?workspaceId=...` — in that case `initialWorkspaceId` is undefined but
  // `state.overview` is real and `state.status` is "ready". Mutating actions
  // gated only on `initialWorkspaceId` would silently route to the
  // `statefulDemo*` in-memory mocks instead of POSTing to the backend, which
  // is exactly the symptom that caused approve/generate/save to look like
  // they did nothing. Always gate on `isLive`, and reach for
  // `effectiveWorkspaceId` whenever an actual workspace id is needed.
  const effectiveWorkspaceId =
    initialWorkspaceId ?? state.overview?.workspace.workspace_id ?? null;
  const isLive = effectiveWorkspaceId !== null && state.status !== "demo";

  const createWorkspace = async (input: CreateWorkspaceInput): Promise<WorkspaceSummary> => {
    const workspace = isLive
      ? await apiClient.createWorkspace(input)
      : statefulDemoCreateWorkspace(input);
    setState((current) => ({
      ...current,
      overview: {
        workspace: {
          workspace_id: workspace.workspaceId,
          customer_name: workspace.customerName,
          project_name: workspace.projectName,
          environment: workspace.environment
        },
        counts: {},
        latestConnectionProfiles: [],
        latestGraphProfiles: [],
        latestSourceDocuments: [],
        latestRequirementVersions: [],
        latestWorkflowRuns: [],
        latestReports: [],
        latestAuditEvents: [
          {
            action: "create_workspace",
            entity_id: workspace.workspaceId,
            actor: input.actor ?? "workspace-ui"
          }
        ]
      },
      health: {
        workspaceId: workspace.workspaceId,
        status: "needs_attention",
        counts: {},
        issues: [
          {
            severity: "warning",
            code: "missing_connection_profile",
            message: "Workspace has no connection profiles.",
            entityIds: []
          }
        ]
      },
      status: current.status === "demo" ? "demo" : "ready",
      errorMessage: undefined
    }));
    return workspace;
  };

  const updateWorkspace = async (
    workspaceId: string,
    input: UpdateWorkspaceInput
  ): Promise<WorkspaceSummary> => {
    if (!isLive) {
      // Demo mode: shallow merge into the in-memory overview so the UI
      // immediately reflects the edit. The real backend re-derives this on
      // the next refreshOverview() call.
      const existing = state.overview?.workspace;
      if (!existing) {
        throw new Error("No workspace loaded");
      }
      const next: WorkspaceSummary = {
        workspaceId,
        customerName: input.customerName?.trim() || existing.customer_name || "",
        projectName: input.projectName?.trim() || existing.project_name || "",
        environment: input.environment?.trim() || existing.environment || "",
        description: input.description?.trim() ?? existing.description ?? "",
        status: existing.status ?? "active",
        tags: input.tags ?? existing.tags ?? [],
        activeGraphProfileId: existing.active_graph_profile_id ?? null
      };
      setState((current) => ({
        ...current,
        overview: current.overview
          ? {
              ...current.overview,
              workspace: {
                ...current.overview.workspace,
                customer_name: next.customerName,
                project_name: next.projectName,
                environment: next.environment,
                description: next.description,
                tags: next.tags
              }
            }
          : current.overview
      }));
      return next;
    }

    const updated = await apiClient.updateWorkspace(workspaceId, input);
    // Re-fetch the overview so any server-side computed fields (counts,
    // updated_at, audit timeline) stay in sync without forcing a full
    // workspace reload.
    await refreshOverview();
    return updated;
  };

  const archiveWorkspace = async (
    workspaceId: string,
    actor = "workspace-ui"
  ): Promise<WorkspaceSummary> => {
    if (!isLive) {
      const existing = state.overview?.workspace;
      if (!existing) {
        throw new Error("No workspace loaded");
      }
      const next: WorkspaceSummary = {
        workspaceId,
        customerName: existing.customer_name ?? "",
        projectName: existing.project_name ?? "",
        environment: existing.environment ?? "",
        description: existing.description ?? "",
        status: "archived",
        tags: existing.tags ?? [],
        activeGraphProfileId: existing.active_graph_profile_id ?? null
      };
      setState((current) => ({
        ...current,
        overview: current.overview
          ? {
              ...current.overview,
              workspace: { ...current.overview.workspace, status: "archived" }
            }
          : current.overview
      }));
      return next;
    }

    const archived = await apiClient.archiveWorkspace(workspaceId, actor);
    await refreshOverview();
    return archived;
  };

  const promoteExtractedRequirements = async (
    documentId: string
  ): Promise<RequirementVersion> => {
    const version = await apiClient.promoteExtractedRequirements(documentId);
    await refreshOverview();
    return version;
  };

  const setActiveGraphProfile = async (
    workspaceId: string,
    graphProfileId: string | null,
    actor = "workspace-ui"
  ): Promise<WorkspaceSummary> => {
    if (!isLive) {
      // Demo mode: optimistically mutate the in-memory overview so the
      // banner switches without a server round-trip. The real backend
      // is the source of truth in live mode; here we just patch the
      // workspace subtree and synthesise a WorkspaceSummary response.
      const existing = state.overview?.workspace;
      if (!existing) {
        throw new Error("No workspace loaded");
      }
      const nextActiveId = graphProfileId ?? null;
      const next: WorkspaceSummary = {
        workspaceId,
        customerName: existing.customer_name ?? "",
        projectName: existing.project_name ?? "",
        environment: existing.environment ?? "",
        description: existing.description ?? "",
        status: existing.status ?? "active",
        tags: existing.tags ?? [],
        activeGraphProfileId: nextActiveId
      };
      setState((current) => ({
        ...current,
        overview: current.overview
          ? {
              ...current.overview,
              workspace: {
                ...current.overview.workspace,
                active_graph_profile_id: nextActiveId
              }
            }
          : current.overview
      }));
      return next;
    }

    const updated = await apiClient.setActiveGraphProfile(
      workspaceId,
      graphProfileId,
      actor
    );
    await refreshOverview();
    return updated;
  };

  const publishReport = async (
    reportId: string,
    actor = "workspace-ui"
  ): Promise<ReportBundle> => {
    if (!isLive) {
      const report = state.reportById[reportId] ?? statefulDemoPublish(reportId);
      const publishedReport = {
        ...report,
        manifest: {
          ...report.manifest,
          status: "published"
        }
      };
      setState((current) => ({
        ...current,
        reportById: {
          ...current.reportById,
          [reportId]: publishedReport
        }
      }));
      return publishedReport;
    }

    const publishedReport = await apiClient.publishReport(reportId, actor);
    setState((current) => ({
      ...current,
      reportById: {
        ...current.reportById,
        [reportId]: publishedReport
      }
    }));
    return publishedReport;
  };

  const exportReport = async (
    reportId: string,
    format: ReportExportFormat
  ): Promise<ReportExportDownload> => {
    if (!isLive) {
      // Demo mode produces a tiny placeholder so the download UI works
      // end-to-end without a backend. The real backend is the only source
      // of truth for the rendered HTML/Markdown.
      const report = state.reportById[reportId] ?? statefulDemoPublish(reportId);
      const stub = `# ${report.manifest.title}\n\nDemo export — no backend connected.\n`;
      return {
        blob: new Blob([stub], {
          type: format === "markdown" ? "text/markdown" : "text/html"
        }),
        filename: `report-${reportId}.${format === "markdown" ? "md" : "html"}`,
        format
      };
    }

    return apiClient.exportReport(reportId, format);
  };

  const createConnectionProfile = async (
    input: CreateConnectionProfileInput
  ): Promise<ConnectionProfileSummary> => {
    const profile = isLive && effectiveWorkspaceId
      ? await apiClient.createConnectionProfile(effectiveWorkspaceId, input)
      : statefulDemoCreateConnectionProfile(input);
    const asset: WorkspaceAsset = {
      id: profile.connectionProfileId,
      kind: "connection-profile",
      label: profile.name,
      description: `${profile.deploymentMode} connection (${profile.lastVerificationStatus})`
    };

    setState((current) => ({
      ...current,
      assets: [asset, ...current.assets.filter((item) => item.id !== asset.id)],
      connectionProfileById: {
        ...current.connectionProfileById,
        [profile.connectionProfileId]: profile
      }
    }));
    return profile;
  };

  const verifyConnectionProfile = async (
    connectionProfileId: string
  ): Promise<ConnectionVerificationResult> => {
    const verification = isLive
      ? await apiClient.verifyConnectionProfile(connectionProfileId)
      : statefulDemoVerifyConnectionProfile(connectionProfileId);

    setState((current) => {
      const profile = current.connectionProfileById[connectionProfileId];
      if (!profile) {
        return current;
      }

      const updatedProfile = {
        ...profile,
        lastVerificationStatus: verification.status,
        lastVerifiedAt: verification.verifiedAt
      };
      return {
        ...current,
        assets: current.assets.map((asset) =>
          asset.id === connectionProfileId
            ? {
                ...asset,
                description: `${updatedProfile.deploymentMode} connection (${updatedProfile.lastVerificationStatus})`
              }
            : asset
        ),
        connectionProfileById: {
          ...current.connectionProfileById,
          [connectionProfileId]: updatedProfile
        }
      };
    });

    return verification;
  };

  const listClusterDatabases = async (
    input: ListClusterDatabasesInput
  ): Promise<ClusterDatabasesResult> => {
    if (isLive) {
      return apiClient.listClusterDatabases(input);
    }
    // Demo mode: return a small canned list so the two-step picker is
    // exercisable without a live cluster.
    return {
      endpoint: input.endpoint,
      databases: ["addtech-knowledge-graph", "FinReflectKG"]
    };
  };

  // Memoised because the connect overlay uses these as effect dependencies.
  // An identity that changes every render re-runs those effects continuously,
  // which re-detected the cluster and undid the user's choice of a different
  // one, and re-fetched defaults over fields they had already edited.
  const deleteWorkflowRun = async (runId: string): Promise<void> => {
    if (!isLive) {
      throw new Error("Connect to a workspace before deleting a run");
    }
    await apiClient.deleteWorkflowRun(runId);
    await refreshOverview();
  };

  const deleteGraphProfile = async (graphProfileId: string): Promise<void> => {
    if (!isLive) {
      throw new Error("Connect to a workspace before deleting a profile");
    }
    await apiClient.deleteGraphProfile(graphProfileId);
    await refreshOverview();
  };

  const deleteConnectionProfile = async (
    connectionProfileId: string
  ): Promise<void> => {
    if (!isLive) {
      throw new Error("Connect to a workspace before deleting a profile");
    }
    await apiClient.deleteConnectionProfile(connectionProfileId);
    // Refresh so the Assets panel reflects the removal; the profile is gone
    // server-side, and hiding it client-side only would repeat the mistake the
    // "Delete Run" action makes.
    await refreshOverview();
  };

  const listDefaultClusterDatabases = useCallback(
    async (): Promise<DefaultClusterDatabasesResult> => {
      if (isLive) {
        return apiClient.listDefaultClusterDatabases();
      }
      // Demo mode: there is no server environment to connect with, so report
      // no default cluster and let the overlay fall back to asking explicitly.
      throw new Error("No default cluster in demo mode");
    },
    [isLive, apiClient]
  );

  const getConnectionDefaults = useCallback(async (): Promise<ConnectionDefaults> => {
    if (isLive) {
      return apiClient.getConnectionDefaults();
    }
    // Demo mode: no server env to read, so return empty defaults and let the
    // form fall back to its own placeholders.
    return {
      endpoint: "",
      username: "",
      database: "",
      verifySsl: true,
      deploymentMode: "",
      passwordSecretEnvVar: "ARANGO_PASSWORD",
      login: "password"
    };
  }, [isLive, apiClient]);

  const uploadSourceDocument = async (
    input: UploadSourceDocumentInput
  ): Promise<SourceDocumentSummary> => {
    if (isLive && effectiveWorkspaceId) {
      const document = await apiClient.uploadSourceDocument(
        effectiveWorkspaceId,
        input
      );
      await refreshOverview();
      return document;
    }
    return statefulDemoUploadSourceDocument(input);
  };

  const createUseCase = async (input: CreateUseCaseInput): Promise<UseCase> => {
    if (isLive && effectiveWorkspaceId) {
      return apiClient.createUseCase(effectiveWorkspaceId, input);
    }
    return {
      ...demoUseCases()[1],
      useCaseId: `use-case-demo-${Date.now()}`,
      title: input.title,
      description: input.description ?? "",
      useCaseType: input.useCaseType ?? "pattern",
      priority: input.priority ?? "medium",
      status: "draft",
      origin: "manual"
    };
  };

  const setUseCaseStatus = async (
    useCaseId: string,
    status: UseCaseStatus,
    reviewNote = ""
  ): Promise<UseCase> => {
    if (isLive) {
      return apiClient.setUseCaseStatus(useCaseId, status, reviewNote);
    }
    const existing =
      demoUseCases().find((item) => item.useCaseId === useCaseId) ?? demoUseCases()[0];
    return { ...existing, status, reviewNote };
  };

  const setUseCasePriority = async (
    useCaseId: string,
    priority: string
  ): Promise<UseCase> => {
    if (isLive) {
      return apiClient.setUseCasePriority(useCaseId, priority);
    }
    const existing =
      demoUseCases().find((item) => item.useCaseId === useCaseId) ?? demoUseCases()[0];
    return { ...existing, priority };
  };

  const createAnalysisTemplate = async (
    input: CreateAnalysisTemplateInput
  ): Promise<AnalysisTemplate> => {
    if (isLive && effectiveWorkspaceId) {
      return apiClient.createAnalysisTemplate(effectiveWorkspaceId, input);
    }
    const id = `analysis-template-demo-${Date.now()}`;
    return {
      ...demoAnalysisTemplates()[1],
      analysisTemplateId: id,
      lineageId: id,
      name: input.name,
      algorithm: input.algorithm,
      description: input.description ?? "",
      parameters: input.parameters ?? {},
      version: 1,
      status: "draft"
    };
  };

  const updateAnalysisTemplate = async (
    analysisTemplateId: string,
    patch: { parameters?: Record<string, unknown>; config?: Record<string, unknown> }
  ): Promise<AnalysisTemplate> => {
    if (isLive) {
      return apiClient.updateAnalysisTemplate(analysisTemplateId, patch);
    }
    const existing =
      demoAnalysisTemplates().find(
        (item) => item.analysisTemplateId === analysisTemplateId
      ) ?? demoAnalysisTemplates()[0];
    // Mirror the FR-25 rule in demo: editing an approved template versions it.
    if (existing.status === "approved") {
      return {
        ...existing,
        ...patch,
        analysisTemplateId: `${existing.analysisTemplateId}-v2`,
        version: existing.version + 1,
        status: "draft"
      };
    }
    return { ...existing, ...patch };
  };

  const approveAnalysisTemplate = async (
    analysisTemplateId: string
  ): Promise<AnalysisTemplate> => {
    if (isLive) {
      return apiClient.approveAnalysisTemplate(analysisTemplateId);
    }
    const existing =
      demoAnalysisTemplates().find(
        (item) => item.analysisTemplateId === analysisTemplateId
      ) ?? demoAnalysisTemplates()[1];
    return { ...existing, status: "approved" };
  };

  const getAnalysisTemplateVersions = async (
    analysisTemplateId: string
  ): Promise<AnalysisTemplate[]> => {
    if (isLive) {
      return apiClient.getAnalysisTemplateVersions(analysisTemplateId);
    }
    const existing = demoAnalysisTemplates().find(
      (item) => item.analysisTemplateId === analysisTemplateId
    );
    return existing ? [existing] : [];
  };

  const importAnalysisTemplates = async (
    templates: Array<Record<string, unknown>>
  ): Promise<AnalysisTemplate[]> => {
    if (isLive && effectiveWorkspaceId) {
      return apiClient.importAnalysisTemplates(effectiveWorkspaceId, templates);
    }
    return templates.map((raw, index) => {
      const id = `analysis-template-demo-import-${index}`;
      return {
        ...demoAnalysisTemplates()[1],
        analysisTemplateId: id,
        lineageId: id,
        name: String(raw.name ?? "Imported template"),
        algorithm: String(raw.algorithm ?? ""),
        description: String(raw.description ?? ""),
        parameters: (raw.parameters as Record<string, unknown>) ?? {},
        version: 1,
        status: "draft" as const
      };
    });
  };

  const DEMO_RETENTION_POLICY: RetentionPolicy = {
    workspaceId: "workspace-demo",
    configured: true,
    enabled: true,
    draftRetentionDays: 30,
    runRetentionDays: 90,
    documentRetentionDays: 0,
    reportSnapshotRetentionDays: 0,
    auditLogRetentionDays: 0,
    lastAppliedAt: null
  };

  // Memoised: consumed as a React effect dependency. An identity that
  // changes every render re-runs that effect continuously, which cancels the
  // in-flight request and starts another — the dropdown never settles.
  const getRetentionPolicy = useCallback(async (): Promise<RetentionPolicy> => {
    if (isLive && effectiveWorkspaceId) {
      return apiClient.getRetentionPolicy(effectiveWorkspaceId);
    }
    return DEMO_RETENTION_POLICY;
  }, [isLive, effectiveWorkspaceId, apiClient]);

  const setRetentionPolicy = async (
    input: SetRetentionPolicyInput
  ): Promise<RetentionPolicy> => {
    if (isLive && effectiveWorkspaceId) {
      return apiClient.setRetentionPolicy(effectiveWorkspaceId, input);
    }
    return { ...DEMO_RETENTION_POLICY, ...input };
  };

  const applyRetentionPolicy = async (
    dryRun = true
  ): Promise<RetentionSweepResult> => {
    if (isLive && effectiveWorkspaceId) {
      return apiClient.applyRetentionPolicy(effectiveWorkspaceId, dryRun);
    }
    return {
      workspaceId: "workspace-demo",
      deleted: !dryRun,
      enabled: true,
      counts: {
        drafts: 1,
        runs: 2,
        documents: 0,
        report_snapshots: 0,
        audit_logs: 0
      },
      candidates: {
        drafts: [
          { id: "requirement-version-demo-old", collection: "aga_requirement_versions", label: "v1 (draft)" }
        ],
        runs: [
          { id: "run-demo-old-1", collection: "aga_workflow_runs", label: "agentic", ephemeral: true },
          { id: "run-demo-old-2", collection: "aga_workflow_runs", label: "agentic" }
        ],
        documents: [],
        report_snapshots: [],
        audit_logs: []
      },
      protected: {
        published_report_ids: ["report-demo"],
        runs_with_published_reports: ["run-demo"]
      },
      ...(dryRun ? {} : { removed: 3 })
    };
  };

  const importVerticalProject = async (
    document: string,
    documentFormat = "yaml"
  ): Promise<VerticalProjectImportResult> => {
    if (isLive && effectiveWorkspaceId) {
      return apiClient.importVerticalProject(
        effectiveWorkspaceId,
        document,
        documentFormat
      );
    }
    return {
      workspaceId: "workspace-demo",
      vertical: "adtech",
      projectName: "Demo bundle",
      counts: { use_cases: 0, templates: 0 },
      warnings: ["Demo mode: nothing was imported."]
    };
  };

  // Memoised: consumed as a React effect dependency. An identity that
  // changes every render re-runs that effect continuously, which cancels the
  // in-flight request and starts another — the dropdown never settles.
  const browseAnalysisCatalog = useCallback(async (): Promise<AnalysisCatalogView> => {
    if (isLive && effectiveWorkspaceId) {
      return apiClient.browseAnalysisCatalog(effectiveWorkspaceId);
    }
    return demoAnalysisCatalog();
  }, [isLive, effectiveWorkspaceId, apiClient]);

  const listAnalysisExecutions = async (
    filters: AnalysisExecutionFilters = {}
  ): Promise<AnalysisExecution[]> => {
    if (isLive && effectiveWorkspaceId) {
      return apiClient.listAnalysisExecutions(effectiveWorkspaceId, filters);
    }
    // Demo mode has no server to filter, so apply the same predicates
    // locally — otherwise the filter controls would look broken in demo.
    return demoAnalysisCatalog().executions.filter((execution) => {
      const startedAt = execution.startedAt ?? "";
      return (
        (!filters.algorithm || execution.algorithm === filters.algorithm) &&
        (!filters.status || execution.status === filters.status) &&
        (!filters.epochId || execution.epochId === filters.epochId) &&
        (!filters.graphProfileId ||
          execution.graphProfileId === filters.graphProfileId) &&
        (!filters.startedAfter || startedAt >= filters.startedAfter) &&
        (!filters.startedBefore || startedAt <= filters.startedBefore)
      );
    });
  };

  const compareAnalysisExecutions = async (
    analysisExecutionIds: string[]
  ): Promise<AnalysisExecutionComparison> => {
    if (isLive && effectiveWorkspaceId) {
      return apiClient.compareAnalysisExecutions(
        effectiveWorkspaceId,
        analysisExecutionIds
      );
    }
    return demoCompareAnalysisExecutions(analysisExecutionIds);
  };

  const getAnalysisLineage = async (
    analysisExecutionId: string
  ): Promise<AnalysisLineage> => {
    if (isLive) {
      return apiClient.getAnalysisLineage(analysisExecutionId);
    }
    const execution =
      demoAnalysisCatalog().executions.find(
        (item) => item.analysisExecutionId === analysisExecutionId
      ) ?? null;
    return {
      workspaceId: "workspace-demo",
      execution,
      reports: [],
      templateId: execution?.templateId ?? null,
      useCaseId: execution?.useCaseId ?? null,
      requirementVersionId: execution?.requirementVersionId ?? null
    };
  };

  // Memoised: consumed as a React effect dependency. An identity that
  // changes every render re-runs that effect continuously, which cancels the
  // in-flight request and starts another — the dropdown never settles.
  const listConnectionProfileGraphs = useCallback(
    async (connectionProfileId: string): Promise<ConnectionGraphsResult> => {
      if (isLive) {
        return apiClient.listConnectionProfileGraphs(connectionProfileId);
      }
      return statefulDemoListConnectionProfileGraphs(connectionProfileId);
    },
    [isLive, apiClient]
  );

  const discoverGraphProfile = async (
    connectionProfileId: string,
    input: DiscoverGraphProfileInput
  ): Promise<GraphDiscoveryResult> => {
    const discovery = isLive
      ? await apiClient.discoverGraphProfile(connectionProfileId, input)
      : statefulDemoDiscoverGraphProfile(connectionProfileId, input);
    const profile = discovery.graphProfile;
    const asset: WorkspaceAsset = {
      id: profile.graphProfileId,
      kind: "graph-profile",
      label: profile.graphName,
      description: `Graph profile (${profile.status})`
    };

    setState((current) => ({
      ...current,
      assets: [asset, ...current.assets.filter((item) => item.id !== asset.id)],
      graphProfileById: {
        ...current.graphProfileById,
        [profile.graphProfileId]: profile
      }
    }));
    return discovery;
  };

  const startRequirementsCopilot = async (
    graphProfileId: string,
    input: StartRequirementsCopilotInput
  ): Promise<RequirementInterview> => {
    return isLive
      ? apiClient.startRequirementsCopilot(graphProfileId, input)
      : statefulDemoStartRequirementsCopilot(graphProfileId, input);
  };

  const answerRequirementsCopilotQuestion = async (
    requirementInterviewId: string,
    questionId: string,
    answer: string,
    actor = "workspace-ui"
  ): Promise<RequirementInterview> => {
    return isLive
      ? apiClient.answerRequirementsCopilotQuestion(
          requirementInterviewId,
          questionId,
          answer,
          actor
        )
      : statefulDemoAnswerRequirementsCopilotQuestion(
          requirementInterviewId,
          questionId,
          answer,
          actor
        );
  };

  const generateRequirementsCopilotDraft = async (
    requirementInterviewId: string
  ): Promise<RequirementsDraftResult> => {
    return isLive
      ? apiClient.generateRequirementsCopilotDraft(requirementInterviewId)
      : statefulDemoGenerateRequirementsCopilotDraft(requirementInterviewId);
  };

  const approveRequirementsCopilotDraft = async (
    requirementInterviewId: string,
    version: number | null,
    approvedBy = "workspace-ui"
  ): Promise<RequirementVersion> => {
    return isLive
      ? apiClient.approveRequirementsCopilotDraft(requirementInterviewId, version, approvedBy)
      : statefulDemoApproveRequirementsCopilotDraft(
          requirementInterviewId,
          version ?? 1,
          approvedBy
        );
  };

  const exportWorkspaceBundle = async (): Promise<WorkspaceBundle> => {
    return isLive && effectiveWorkspaceId
      ? apiClient.exportWorkspaceBundle(effectiveWorkspaceId)
      : statefulDemoExportWorkspaceBundle(state);
  };

  const importWorkspaceBundle = async (
    bundle: WorkspaceBundle
  ): Promise<WorkspaceImportResult> => {
    return isLive
      ? apiClient.importWorkspaceBundle(bundle)
      : statefulDemoImportWorkspaceBundle(bundle);
  };

  const createWorkflowRun = async (
    input: CreateWorkflowRunInput
  ): Promise<CreateWorkflowRunResult> => {
    const workspaceId = effectiveWorkspaceId ?? demoDag.workspaceId;
    const result = isLive
      ? await apiClient.createWorkflowRun(workspaceId, input)
      : statefulDemoCreateWorkflowRun(workspaceId, input);
    const asset: WorkspaceAsset = {
      id: result.workflowRun.runId,
      kind: "run",
      label: `Run ${result.workflowRun.runId}`,
      description: `${result.workflowRun.workflowMode} workflow (${result.workflowRun.status})`
    };

    setState((current) => ({
      ...current,
      assets: [asset, ...current.assets.filter((item) => item.id !== asset.id)],
      dagByRunId: {
        ...current.dagByRunId,
        [result.workflowRun.runId]: result.dagView
      },
      recoveryActionsByRunId: {
        ...current.recoveryActionsByRunId,
        [result.workflowRun.runId]: demoRecoveryActions(result.dagView)
      }
    }));

    return result;
  };

  const quickAnalysis = async (
    input: QuickAnalysisInput
  ): Promise<CreateWorkflowRunResult> => {
    const workspaceId = effectiveWorkspaceId ?? demoDag.workspaceId;
    const result = isLive
      ? await apiClient.quickAnalysis(workspaceId, input)
      : statefulDemoCreateWorkflowRun(workspaceId, {
          workflowMode: input.workflowMode ?? "agentic",
          stepLabels: [
            "Schema Analysis",
            "Requirements Extraction",
            "Use Case Generation",
            "Template Generation",
            "Execution",
            "Reporting"
          ]
        });
    const asset: WorkspaceAsset = {
      id: result.workflowRun.runId,
      kind: "run",
      label: `Run ${result.workflowRun.runId}`,
      description: `${result.workflowRun.workflowMode} workflow (${result.workflowRun.status})`
    };

    setState((current) => ({
      ...current,
      assets: [asset, ...current.assets.filter((item) => item.id !== asset.id)],
      dagByRunId: {
        ...current.dagByRunId,
        [result.workflowRun.runId]: result.dagView
      },
      recoveryActionsByRunId: {
        ...current.recoveryActionsByRunId,
        [result.workflowRun.runId]: demoRecoveryActions(result.dagView)
      }
    }));

    return result;
  };

  const startWorkflowRun = async (runId: string): Promise<WorkflowRunSummary> => {
    const workflowRun = isLive
      ? await apiClient.startWorkflowRun(runId)
      : statefulDemoStartWorkflowRun(runId);

    setState((current) => ({
      ...current,
      assets: current.assets.map((asset) =>
        asset.id === runId
          ? {
              ...asset,
              description: `${workflowRun.workflowMode} workflow (${workflowRun.status})`
            }
          : asset
      ),
      dagByRunId: current.dagByRunId[runId]
        ? {
            ...current.dagByRunId,
            [runId]: {
              ...current.dagByRunId[runId],
              status: workflowRun.status
            }
          }
        : current.dagByRunId
    }));

    return workflowRun;
  };

  const cancelWorkflowRun = async (
    runId: string,
    actor?: string
  ): Promise<WorkflowRunSummary> => {
    // FR-31a: deliver a cooperative cancel to the supervisor. The
    // returned row may still show ``running`` momentarily because the
    // orchestrator only polls the cancel token between steps; the
    // canvas + status poller will reflect the eventual ``cancelled``
    // status on its next refresh.
    if (!isLive) {
      // Demo mode: synchronous flip so the visualizer reflects the
      // user's intent immediately.
      const demoRun: WorkflowRunSummary = {
        runId,
        workspaceId: state.overview?.workspace.workspace_id ?? "demo-workspace",
        workflowMode: "agentic",
        status: "cancelled",
        startedAt: null,
        completedAt: new Date().toISOString()
      };
      setState((current) => ({
        ...current,
        assets: current.assets.map((asset) =>
          asset.id === runId
            ? { ...asset, description: `agentic workflow (cancelled)` }
            : asset
        ),
        dagByRunId: current.dagByRunId[runId]
          ? {
              ...current.dagByRunId,
              [runId]: { ...current.dagByRunId[runId], status: "cancelled" }
            }
          : current.dagByRunId
      }));
      return demoRun;
    }
    const workflowRun = await apiClient.cancelWorkflowRun(runId, actor);
    setState((current) => ({
      ...current,
      assets: current.assets.map((asset) =>
        asset.id === runId
          ? {
              ...asset,
              description: `${workflowRun.workflowMode} workflow (${workflowRun.status})`
            }
          : asset
      ),
      dagByRunId: current.dagByRunId[runId]
        ? {
            ...current.dagByRunId,
            [runId]: { ...current.dagByRunId[runId], status: workflowRun.status }
          }
        : current.dagByRunId
    }));
    return workflowRun;
  };

  const getWorkflowRunStatus = async (
    runId: string
  ): Promise<WorkflowRunStatusView | null> => {
    // FR-31a: returns null in demo mode so callers can render the
    // poll panel only when there's something real to poll for.
    //
    // `isLive` deliberately means "not demo", so it is already true while
    // status === "loading" (the workspace id comes from the URL, before the
    // fetch lands). In that window the selected asset is still the demo seed,
    // so polling sent the placeholder id to a real backend and logged two 404s
    // on /api/runs/run-demo/status for every cold load. A status poll is
    // meaningless before the workspace has settled, so require "ready" here
    // rather than widening `isLive`, which many mutating actions depend on
    // being permissive.
    if (!isLive || state.status !== "ready") {
      return null;
    }
    return apiClient.getWorkflowRunStatus(runId);
  };

  const updateWorkflowStep = async (
    runId: string,
    stepId: string,
    status: WorkflowStepStatus
  ): Promise<WorkflowStepUpdateResult> => {
    const result = isLive
      ? await apiClient.updateWorkflowStep(runId, stepId, status)
      : statefulDemoUpdateWorkflowStep(runId, stepId, status);

    setState((current) => ({
      ...current,
      assets: current.assets.map((asset) =>
        asset.id === runId
          ? {
              ...asset,
              description: `${result.workflowRun.workflowMode} workflow (${result.workflowRun.status})`
            }
          : asset
      ),
      dagByRunId: {
        ...current.dagByRunId,
        [runId]: result.dagView
      },
      recoveryActionsByRunId: {
        ...current.recoveryActionsByRunId,
        [runId]: demoRecoveryActions(result.dagView)
      }
    }));

    return result;
  };

  const requirementVersions = state.overview?.latestRequirementVersions ?? [];
  // The "active" version is the most-recent APPROVED one; if none is APPROVED
  // (e.g. only DRAFT exists, or all prior versions were SUPERSEDED before a
  // new one landed) we pick the highest-numbered version as the displayed
  // version so the UI never shows an empty requirements asset for a workspace
  // that actually has versions.
  const approvedRequirementVersion =
    requirementVersions.find((version) => version.status === "approved") ??
    requirementVersions[0] ??
    null;

  // Targeted re-fetch of the workspace overview only. Used after mutations
  // (currently: approve Requirements Copilot draft) so the AssetExplorer's
  // consolidated "Requirements" row and the version selector reflect the new
  // state without forcing a full page reload.
  const refreshOverview = async (): Promise<void> => {
    if (!isLive || !effectiveWorkspaceId) {
      return;
    }
    try {
      const overview = await apiClient.getWorkspaceOverview(effectiveWorkspaceId);
      const assets = workspaceAssetsFromOverview(overview);
      setState((current) => ({
        ...current,
        overview,
        assets: assets.length > 0 ? assets : current.assets
      }));
    } catch {
      // Silent: a failed refresh leaves the existing state intact, which is
      // safer than wiping it. The next user-triggered load will retry.
    }
  };

  return {
    ...state,
    createWorkspace,
    updateWorkspace,
    archiveWorkspace,
    setActiveGraphProfile,
    publishReport,
    exportReport,
    createConnectionProfile,
    verifyConnectionProfile,
    listClusterDatabases,
    listDefaultClusterDatabases,
    deleteConnectionProfile,
    deleteGraphProfile,
    deleteWorkflowRun,
    getConnectionDefaults,
    uploadSourceDocument,
    createUseCase,
    setUseCaseStatus,
    setUseCasePriority,
    createAnalysisTemplate,
    updateAnalysisTemplate,
    approveAnalysisTemplate,
    getAnalysisTemplateVersions,
    importAnalysisTemplates,
    getRetentionPolicy,
    setRetentionPolicy,
    applyRetentionPolicy,
    importVerticalProject,
    browseAnalysisCatalog,
    listAnalysisExecutions,
    compareAnalysisExecutions,
    getAnalysisLineage,
    listConnectionProfileGraphs,
    discoverGraphProfile,
    startRequirementsCopilot,
    answerRequirementsCopilotQuestion,
    generateRequirementsCopilotDraft,
    approveRequirementsCopilotDraft,
    refreshOverview,
    promoteExtractedRequirements,
    exportWorkspaceBundle,
    importWorkspaceBundle,
    createWorkflowRun,
    quickAnalysis,
    startWorkflowRun,
    cancelWorkflowRun,
    getWorkflowRunStatus,
    updateWorkflowStep,
    approvedRequirementVersion,
    requirementVersions
  };
}

function statefulDemoPublish(reportId: string): ReportBundle {
  return {
    ...demoReport,
    manifest: {
      ...demoReport.manifest,
      reportId,
      status: "published"
    }
  };
}

function statefulDemoCreateWorkspace(input: CreateWorkspaceInput): WorkspaceSummary {
  return {
    workspaceId: `workspace-${Date.now()}`,
    customerName: input.customerName.trim(),
    projectName: input.projectName.trim(),
    environment: input.environment.trim(),
    description: input.description?.trim() ?? "",
    status: "active",
    tags: input.tags ?? [],
    activeGraphProfileId: null
  };
}

function demoRecoveryActions(dag: WorkflowDAGView): WorkflowRecoveryActions {
  return Object.fromEntries(
    dag.nodes.map((node) => [
      node.id,
      node.status === "failed"
        ? ["retry", "open_logs"]
        : node.status === "paused"
          ? ["resume", "cancel", "open_logs"]
          : []
    ])
  );
}

function statefulDemoCreateConnectionProfile(
  input: CreateConnectionProfileInput
): ConnectionProfileSummary {
  return {
    connectionProfileId: `connection-${Date.now()}`,
    workspaceId: "workspace-demo",
    name: input.name,
    deploymentMode: input.deploymentMode,
    endpoint: input.endpoint,
    database: input.database,
    username: input.username,
    verifySsl: input.verifySsl,
    secretRefs: connectionSecretRefs(input),
    lastVerificationStatus: "unknown",
    lastVerifiedAt: null,
    metadata: { source: "demo" }
  };
}

function statefulDemoVerifyConnectionProfile(
  connectionProfileId: string
): ConnectionVerificationResult {
  return {
    connectionProfileId,
    workspaceId: "workspace-demo",
    status: "success",
    verifiedAt: new Date().toISOString(),
    endpoint: demoConnectionProfile.endpoint,
    database: demoConnectionProfile.database,
    errorMessage: null
  };
}

/** Demo catalog fixture. Field shapes mirror the real service payloads. */
function demoAnalysisCatalog(): AnalysisCatalogView {
  const epochId = "analysis-epoch-demo";
  const baseExecution = {
    workspaceId: "workspace-demo",
    runId: "run-demo",
    graphProfileId: "graph-profile-demo",
    requirementVersionId: "requirement-version-demo",
    epochId,
    algorithmVersion: "1.0",
    parameters: {},
    workflowMode: "agentic",
    completedAt: null,
    errorMessage: null,
    metadata: {}
  };

  return {
    workspaceId: "workspace-demo",
    epochs: [
      {
        analysisEpochId: epochId,
        workspaceId: "workspace-demo",
        name: "2026-Q3 snapshot",
        description: "Quarterly analysis snapshot",
        timestamp: "2026-07-01T00:00:00Z",
        status: "active",
        tags: ["quarterly"],
        analysisCount: 3,
        analysisExecutionIds: [
          "analysis-execution-demo-1",
          "analysis-execution-demo-2",
          "analysis-execution-demo-3"
        ]
      }
    ],
    executions: [
      {
        ...baseExecution,
        analysisExecutionId: "analysis-execution-demo-1",
        algorithm: "pagerank",
        status: "completed",
        useCaseId: "use-case-demo-1",
        templateId: "template-demo-1",
        templateName: "PageRank on Person",
        resultsLocation: "pagerank_results",
        resultCount: 1200,
        performanceMetrics: { duration_ms: 4200 },
        startedAt: "2026-07-01T09:00:00Z"
      },
      {
        ...baseExecution,
        analysisExecutionId: "analysis-execution-demo-2",
        algorithm: "wcc",
        status: "completed",
        useCaseId: "use-case-demo-2",
        templateId: "template-demo-2",
        templateName: "Weakly Connected Components",
        resultsLocation: "wcc_results",
        resultCount: 800,
        performanceMetrics: { duration_ms: 2100 },
        startedAt: "2026-07-01T09:10:00Z"
      },
      {
        ...baseExecution,
        analysisExecutionId: "analysis-execution-demo-3",
        algorithm: "pagerank",
        status: "failed",
        useCaseId: "use-case-demo-1",
        templateId: "template-demo-1",
        templateName: "PageRank on Person",
        resultsLocation: null,
        resultCount: 0,
        performanceMetrics: {},
        errorMessage: "Engine ran out of memory during projection",
        startedAt: "2026-07-02T09:00:00Z"
      }
    ],
    templates: demoAnalysisTemplates(),
    useCases: demoUseCases(),
    requirements: [{ requirement_version_id: "requirement-version-demo" }],
    unresolvedTemplateIds: [],
    unresolvedUseCaseIds: []
  };
}

/** Demo use cases (FR-19..FR-21). Shapes mirror UseCase.to_dict. */
function demoUseCases(): UseCase[] {
  const base = {
    workspaceId: "workspace-demo",
    requirementVersionId: "requirement-version-demo",
    relatedRequirements: ["REQ-001"],
    dataNeeds: ["Account", "transfers"],
    reviewedBy: null,
    reviewedAt: null,
    reviewNote: "",
    createdAt: "2026-07-01T08:00:00Z",
    createdBy: "analyst@example.com"
  };
  return [
    {
      ...base,
      useCaseId: "use-case-demo-1",
      title: "Rank accounts by influence",
      description: "Identify the most influential accounts in the network.",
      useCaseType: "centrality",
      priority: "high",
      status: "approved",
      origin: "generated",
      graphAlgorithms: ["pagerank"],
      expectedOutputs: ["ranked account list"],
      successMetrics: ["top-50 reviewed by analysts"]
    },
    {
      ...base,
      useCaseId: "use-case-demo-2",
      title: "Find fraud rings",
      description: "Detect tightly connected clusters of colluding accounts.",
      useCaseType: "community",
      priority: "critical",
      status: "draft",
      origin: "manual",
      graphAlgorithms: ["wcc", "label_propagation"],
      expectedOutputs: ["ring clusters"],
      successMetrics: ["precision > 0.8"]
    }
  ];
}

/** Demo templates (FR-22..FR-26). Shapes mirror AnalysisTemplate.to_dict. */
function demoAnalysisTemplates(): AnalysisTemplate[] {
  const base = {
    workspaceId: "workspace-demo",
    config: { graph_name: "customer_graph", result_collection: "results" },
    supersededBy: null,
    createdAt: "2026-07-01T08:30:00Z",
    metadata: {}
  };
  return [
    {
      ...base,
      analysisTemplateId: "analysis-template-demo-1",
      lineageId: "analysis-template-demo-1",
      name: "PageRank on Person",
      description: "Rank people by influence.",
      algorithm: "pagerank",
      parameters: { damping_factor: 0.85, max_iterations: 20 },
      version: 1,
      status: "approved",
      useCaseId: "use-case-demo-1",
      estimatedRuntimeSeconds: 45,
      approvedBy: "approver@example.com",
      approvedAt: "2026-07-01T09:00:00Z"
    },
    {
      ...base,
      analysisTemplateId: "analysis-template-demo-2",
      lineageId: "analysis-template-demo-2",
      name: "Weakly Connected Components",
      description: "Cluster the graph into connected components.",
      algorithm: "wcc",
      parameters: {},
      version: 1,
      status: "draft",
      useCaseId: "use-case-demo-2",
      estimatedRuntimeSeconds: null,
      approvedBy: null,
      approvedAt: null
    }
  ];
}

function demoCompareAnalysisExecutions(
  analysisExecutionIds: string[]
): AnalysisExecutionComparison {
  const all = demoAnalysisCatalog().executions;
  const selected = analysisExecutionIds
    .map((id) => all.find((item) => item.analysisExecutionId === id))
    .filter((item): item is AnalysisExecution => Boolean(item));
  const baseline = selected[0];

  return {
    workspaceId: "workspace-demo",
    baselineExecutionId: baseline?.analysisExecutionId ?? "",
    executions: selected,
    deltas: selected.map((execution) => {
      const metricKeys = new Set([
        ...Object.keys(baseline?.performanceMetrics ?? {}),
        ...Object.keys(execution.performanceMetrics)
      ]);
      const performanceMetrics: Record<string, number> = {};
      for (const key of metricKeys) {
        performanceMetrics[key] =
          (execution.performanceMetrics[key] ?? 0) -
          (baseline?.performanceMetrics[key] ?? 0);
      }
      return {
        analysisExecutionId: execution.analysisExecutionId,
        resultCount: execution.resultCount - (baseline?.resultCount ?? 0),
        performanceMetrics
      };
    })
  };
}

function statefulDemoUploadSourceDocument(
  input: UploadSourceDocumentInput
): SourceDocumentSummary {
  // Demo mode has no server to parse the file, so the extracted text is
  // just the decoded payload for text-ish uploads (binary formats need a
  // real parser and are reported as not-extracted-in-demo).
  let extractedText: string | null = null;
  try {
    extractedText = atob(input.contentBase64);
  } catch {
    extractedText = null;
  }

  return {
    documentId: `document-demo-${Date.now()}`,
    workspaceId: "workspace-demo",
    filename: input.filename,
    mimeType: input.mimeType,
    sha256: "demo-not-computed",
    storageMode: "extract_only",
    storageUri: null,
    extractedText,
    uploadedAt: new Date().toISOString(),
    metadata: { source: "demo" }
  };
}

function statefulDemoListConnectionProfileGraphs(
  connectionProfileId: string
): ConnectionGraphsResult {
  return {
    connectionProfileId,
    workspaceId: "workspace-demo",
    database: demoConnectionProfile.database,
    graphs: [
      {
        name: demoGraphProfile.graphName,
        isSystem: false,
        vertexCollections: demoGraphProfile.vertexCollections,
        edgeCollections: demoGraphProfile.edgeCollections,
        orphanCollections: [],
        edgeDefinitions: demoGraphProfile.edgeDefinitions,
        vertexCount: demoGraphProfile.counts?.total_documents ?? null,
        edgeCount: demoGraphProfile.counts?.total_edges ?? null
      }
    ]
  };
}

function statefulDemoDiscoverGraphProfile(
  connectionProfileId: string,
  input: DiscoverGraphProfileInput
): GraphDiscoveryResult {
  const graphProfile = {
    ...demoGraphProfile,
    graphProfileId: `graph-profile-${Date.now()}`,
    connectionProfileId,
    graphName: input.graphName?.trim() || demoGraphProfile.graphName,
    status: "active"
  };
  return {
    graphProfile,
    schemaSummary: {
      database_name: demoConnectionProfile.database,
      graph_names: [graphProfile.graphName],
      sample_size: input.sampleSize
    }
  };
}

function statefulDemoStartRequirementsCopilot(
  graphProfileId: string,
  input: StartRequirementsCopilotInput
): RequirementInterview {
  const graphProfile = demoGraphProfile.graphProfileId === graphProfileId
    ? demoGraphProfile
    : { ...demoGraphProfile, graphProfileId };
  demoRequirementInterview = {
    requirementInterviewId: `requirement-interview-${Date.now()}`,
    workspaceId: graphProfile.workspaceId,
    graphProfileId,
    status: "draft",
    domain: input.domain?.trim() || null,
    questions: [
      {
        id: "business_goal",
        text: `What business decision should ${graphProfile.graphName} support?`,
        provenance: "user_provided"
      },
      {
        id: "analytics_questions",
        text: "What graph analytics questions should the system answer?",
        provenance: "user_provided"
      },
      {
        id: "audience",
        text: "Who will consume the report and what level of detail do they need?",
        provenance: "user_provided"
      },
      {
        id: "constraints",
        text: "What runtime, cost, freshness, sensitivity, or evidence constraints apply?",
        provenance: "user_provided"
      }
    ],
    answers: [],
    schemaObservations: {
      graph_name: graphProfile.graphName,
      vertex_collections: graphProfile.vertexCollections,
      edge_collections: graphProfile.edgeCollections,
      counts: graphProfile.counts
    },
    inferences: [],
    assumptions: [],
    draftBrd: null,
    provenanceLabels: []
  };
  return demoRequirementInterview;
}

function statefulDemoAnswerRequirementsCopilotQuestion(
  requirementInterviewId: string,
  questionId: string,
  answer: string,
  actor: string
): RequirementInterview {
  const interview =
    demoRequirementInterview?.requirementInterviewId === requirementInterviewId
      ? demoRequirementInterview
      : statefulDemoStartRequirementsCopilot(demoGraphProfile.graphProfileId, {});
  const answers = [
    ...interview.answers.filter((existing) => existing.question_id !== questionId),
    {
      question_id: questionId,
      answer,
      actor,
      answered_at: new Date().toISOString()
    }
  ];
  demoRequirementInterview = {
    ...interview,
    requirementInterviewId,
    answers
  };
  return demoRequirementInterview;
}

function statefulDemoGenerateRequirementsCopilotDraft(
  requirementInterviewId: string
): RequirementsDraftResult {
  const currentInterview =
    demoRequirementInterview?.requirementInterviewId === requirementInterviewId
      ? demoRequirementInterview
      : statefulDemoStartRequirementsCopilot(demoGraphProfile.graphProfileId, {});
  const requirementInterview = {
    ...currentInterview,
    requirementInterviewId,
    status: "ready_for_review",
    draftBrd:
      "# Business Requirements Draft\n\nThis draft was generated from demo schema observations and saved interview answers."
  };
  demoRequirementInterview = requirementInterview;
  return {
    requirementInterview,
    draftBrd: requirementInterview.draftBrd ?? "",
    provenanceLabels: [
      { path: "observed_schema.graph_name", label: "observed_from_schema" },
      { path: "answers", label: "user_provided" }
    ]
  };
}

function statefulDemoApproveRequirementsCopilotDraft(
  requirementInterviewId: string,
  version: number,
  approvedBy: string
): RequirementVersion {
  const currentInterview =
    demoRequirementInterview?.requirementInterviewId === requirementInterviewId
      ? demoRequirementInterview
      : statefulDemoStartRequirementsCopilot(demoGraphProfile.graphProfileId, {});
  demoRequirementInterview = {
    ...currentInterview,
    status: "approved"
  };
  return {
    requirementVersionId: `requirement-version-${Date.now()}`,
    workspaceId: currentInterview.workspaceId,
    version,
    status: "approved",
    requirementInterviewId,
    // Copilot-authored: no source documents, so nothing to attribute it to.
    documentIds: [],
    summary: "Requirements Copilot approved draft",
    objectives: [],
    requirements: [],
    constraints: [],
    approvedAt: new Date().toISOString(),
    metadata: {
      approved_by: approvedBy,
      source: "requirements_copilot"
    }
  };
}

function statefulDemoExportWorkspaceBundle(state: WorkspaceDataState): WorkspaceBundle {
  return {
    schemaVersion: "demo",
    workspace: {
      workspace_id: state.overview?.workspace.workspace_id ?? "workspace-demo",
      customer_name: state.overview?.workspace.customer_name ?? "Demo Customer",
      project_name: state.overview?.workspace.project_name ?? "Graph Analytics",
      environment: state.overview?.workspace.environment ?? "demo"
    },
    connectionProfiles: toRecordArray(Object.values(state.connectionProfileById)),
    graphProfiles: toRecordArray(Object.values(state.graphProfileById)),
    sourceDocuments: toRecordArray(Object.values(state.documentById)),
    requirementInterviews: demoRequirementInterview ? toRecordArray([demoRequirementInterview]) : [],
    requirementVersions: [],
    workflowRuns: toRecordArray(Object.values(state.dagByRunId)),
    reports: toRecordArray(Object.values(state.reportById)),
    auditEvents: []
  };
}

function toRecordArray<T>(items: T[]): Array<Record<string, unknown>> {
  return items.map((item) => ({ ...(item as object) }));
}

function statefulDemoImportWorkspaceBundle(bundle: WorkspaceBundle): WorkspaceImportResult {
  return {
    workspaceId: String(bundle.workspace.workspace_id ?? bundle.workspace._key ?? "workspace-demo"),
    counts: {
      connection_profiles: bundle.connectionProfiles.length,
      graph_profiles: bundle.graphProfiles.length,
      source_documents: bundle.sourceDocuments.length,
      requirement_versions: bundle.requirementVersions.length,
      workflow_runs: bundle.workflowRuns.length,
      reports: bundle.reports.length
    }
  };
}

function statefulDemoCreateWorkflowRun(
  workspaceId: string,
  input: CreateWorkflowRunInput
): CreateWorkflowRunResult {
  const runId = `run-${Date.now()}`;
  const nodes = input.stepLabels.map((label, index) => ({
    id: slugifyStepId(label, index),
    label,
    status: "pending" as const,
    artifactCount: 0,
    warningCount: 0,
    errorCount: 0
  }));
  const dagView = {
    runId,
    workspaceId,
    status: "queued",
    workflowMode: input.workflowMode,
    nodes,
    edges: nodes.slice(1).map((node, index) => ({
      id: `${nodes[index].id}-${node.id}`,
      from: nodes[index].id,
      to: node.id
    })),
    warnings: [],
    errors: []
  };
  return {
    workflowRun: {
      runId,
      workspaceId,
      workflowMode: input.workflowMode,
      status: "queued"
    },
    dagView
  };
}

function slugifyStepId(label: string, index: number): string {
  const slug = label
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
  return slug || `step-${index + 1}`;
}

function statefulDemoStartWorkflowRun(runId: string): WorkflowRunSummary {
  return {
    runId,
    workspaceId: demoDag.workspaceId,
    workflowMode: demoDag.workflowMode,
    status: "running",
    startedAt: new Date().toISOString(),
    completedAt: null
  };
}

function statefulDemoUpdateWorkflowStep(
  runId: string,
  stepId: string,
  status: WorkflowStepStatus
): WorkflowStepUpdateResult {
  const currentDag = demoDag.runId === runId ? demoDag : { ...demoDag, runId };
  const dagView = {
    ...currentDag,
    status: status === "running" ? "running" : currentDag.status,
    nodes: currentDag.nodes.map((node) =>
      node.id === stepId
        ? {
            ...node,
            status
          }
        : node
    )
  };
  return {
    workflowRun: {
      runId,
      workspaceId: currentDag.workspaceId,
      workflowMode: currentDag.workflowMode,
      status: dagView.status
    },
    dagView
  };
}
