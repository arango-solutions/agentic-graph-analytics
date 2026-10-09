export type WorkflowStepStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "skipped"
  | "paused"
  // FR-31a AC#5: distinguishes "agent didn't run because the run
  // was cancelled" from "agent ran and failed". Surfaced by the
  // backend when ``cancel_workflow_run`` finalizes — the supervisor
  // flips any in-flight / pending step to ``cancelled`` so the DAG
  // doesn't show a permanent ``running`` stripe.
  | "cancelled";

export type WorkspaceAssetKind =
  | "connection-profile"
  | "document"
  | "graph-profile"
  // The Assets panel surfaces ONE consolidated "Requirements" row per
  // workspace, even when several RequirementVersion records exist (v1, v2,…).
  // The asset id is synthetic — `requirements:<workspaceId>` — and the canvas
  // exposes a version selector dropdown to view active or any historical
  // version. This avoids the v1/v2/v3/… clutter the per-version model
  // produced and matches how users think about "the Requirements doc."
  | "requirements"
  // FR-45..FR-48: ONE consolidated "Analysis Catalog" row per workspace,
  // synthetic id `analysis-catalog:<workspaceId>`, same consolidation trick
  // as "requirements" above. Unlike Requirements this row is shown even when
  // empty — it is the only entry point to the catalog, so hiding it when no
  // analyses have run yet would make the feature undiscoverable.
  | "analysis-catalog"
  // FR-19..FR-26: one consolidated "Use Cases & Templates" row per workspace.
  | "use-cases"
  // FR-54: one consolidated "Retention" admin row per workspace. Always shown,
  // because "no policy configured" is itself the thing an admin needs to see.
  | "retention"
  | "run"
  | "report";

export interface WorkspaceAsset {
  id: string;
  kind: WorkspaceAssetKind;
  label: string;
  description?: string;
}

export interface WorkspaceSummary {
  workspaceId: string;
  customerName: string;
  projectName: string;
  environment: string;
  description: string;
  status: string;
  tags: string[];
  /** GraphProfile the workbench should treat as the workspace's "current"
   * graph (FR-67b). When null, the workbench falls back to the most
   * recently-updated profile. Selectable via PATCH
   * /api/workspaces/{id}/active-graph-profile. */
  activeGraphProfileId: string | null;
}

export interface CreateWorkspaceInput {
  customerName: string;
  projectName: string;
  environment: string;
  description?: string;
  tags?: string[];
  actor?: string;
}

/** Patch payload for ``PATCH /api/workspaces/{id}``. Every field is optional;
 * only the fields explicitly set are updated server-side. ``status`` is
 * intentionally NOT here — use the dedicated archive route so the lifecycle
 * change always emits a typed audit event instead of being mixed into a
 * generic update diff. */
export interface UpdateWorkspaceInput {
  customerName?: string;
  projectName?: string;
  environment?: string;
  description?: string;
  tags?: string[];
  actor?: string;
}

export interface GraphProfileSummary {
  graphProfileId: string;
  workspaceId: string;
  connectionProfileId: string;
  graphName: string;
  status: string;
  version: number;
  vertexCollections: string[];
  edgeCollections: string[];
  edgeDefinitions: Array<Record<string, unknown>>;
  collectionRoles: Record<string, string[]>;
  counts: Record<string, number>;
  /** FR-65: deployment sharding/tenancy layout, read from ArangoDB during
   * discovery. Absent on profiles discovered before FR-65 shipped. */
  shardingProfile?: ShardingProfile | null;
}

/** FR-65. Mirrors ShardingProfile.to_dict from ai/schema/sharding.py. */
export interface ShardingProfile {
  deploymentKind: string;
  isOneShard: boolean;
  isMultitenant: boolean;
  tenantKey?: string | null;
  shardKeys: string[];
  smartGraphAttributes: string[];
  maxNumberOfShards: number;
  minReplicationFactor?: number | null;
  satelliteCollections: string[];
  warnings: string[];
}

export interface RawShardingProfile {
  deployment_kind?: string;
  is_one_shard?: boolean;
  is_multitenant?: boolean;
  tenant_key?: string | null;
  shard_keys?: string[];
  smart_graph_attributes?: string[];
  max_number_of_shards?: number;
  min_replication_factor?: number | null;
  satellite_collections?: string[];
  warnings?: string[];
}

/* --- Retention (FR-54) --- */

export interface RetentionPolicy {
  workspaceId: string;
  configured: boolean;
  enabled: boolean;
  draftRetentionDays: number;
  runRetentionDays: number;
  documentRetentionDays: number;
  reportSnapshotRetentionDays: number;
  auditLogRetentionDays: number;
  lastAppliedAt?: string | null;
}

export interface RetentionSweepCandidate {
  id: string;
  collection: string;
  label?: string;
  ephemeral?: boolean;
}

export interface RetentionSweepResult {
  workspaceId: string;
  /** false for a dry run — the default. */
  deleted: boolean;
  enabled: boolean;
  reason?: string;
  counts: Record<string, number>;
  candidates: Record<string, RetentionSweepCandidate[]>;
  protected: {
    published_report_ids?: string[];
    runs_with_published_reports?: string[];
  };
  removed?: number;
}

export interface SetRetentionPolicyInput {
  enabled?: boolean;
  draftRetentionDays?: number;
  runRetentionDays?: number;
  documentRetentionDays?: number;
  reportSnapshotRetentionDays?: number;
  auditLogRetentionDays?: number;
}

export interface RawRetentionPolicy {
  workspace_id: string;
  configured?: boolean;
  enabled?: boolean;
  draft_retention_days?: number;
  run_retention_days?: number;
  document_retention_days?: number;
  report_snapshot_retention_days?: number;
  audit_log_retention_days?: number;
  last_applied_at?: string | null;
}

export interface RawRetentionSweepResult {
  workspace_id: string;
  deleted?: boolean;
  enabled?: boolean;
  reason?: string;
  counts?: Record<string, number>;
  candidates?: Record<string, RetentionSweepCandidate[]>;
  protected?: Record<string, string[]>;
  removed?: number;
}

/* --- Vertical project import (FR-49 / FR-50) --- */

export interface VerticalProjectImportResult {
  workspaceId: string;
  vertical: string;
  projectName: string;
  counts: { use_cases: number; templates: number };
  warnings: string[];
}

export interface RawVerticalProjectImportResult {
  workspace_id: string;
  vertical?: string;
  project_name?: string;
  counts?: { use_cases: number; templates: number };
  warnings?: string[];
}

export interface SourceDocumentSummary {
  documentId: string;
  workspaceId: string;
  filename: string;
  mimeType: string;
  sha256: string;
  storageMode: string;
  storageUri?: string | null;
  extractedText?: string | null;
  uploadedAt?: string;
  metadata: Record<string, unknown>;
}

/* --------------------------------------------------------------------------
 * Analysis Catalog (FR-45..FR-48)
 *
 * Field names below mirror the exact dicts ProductService returns
 * (AnalysisExecution.to_dict / AnalysisEpoch.to_dict and the browse /
 * compare / lineage projections), captured by running the service rather
 * than read off the Python type hints.
 * ----------------------------------------------------------------------- */

/* --------------------------------------------------------------------------
 * Use Cases and Analysis Templates (FR-19..FR-26)
 * Field names mirror UseCase.to_dict / AnalysisTemplate.to_dict, captured by
 * running the service rather than read off its type hints.
 * ----------------------------------------------------------------------- */

export type UseCaseStatus = "draft" | "approved" | "rejected" | "archived";
export type AnalysisTemplateStatus =
  | "draft"
  | "approved"
  | "superseded"
  | "archived";

export interface UseCase {
  useCaseId: string;
  workspaceId: string;
  title: string;
  description: string;
  useCaseType: string;
  priority: string;
  status: UseCaseStatus;
  origin: string;
  requirementVersionId?: string | null;
  relatedRequirements: string[];
  graphAlgorithms: string[];
  dataNeeds: string[];
  expectedOutputs: string[];
  successMetrics: string[];
  reviewedBy?: string | null;
  reviewedAt?: string | null;
  reviewNote: string;
  createdAt?: string;
  createdBy?: string | null;
}

export interface AnalysisTemplate {
  analysisTemplateId: string;
  workspaceId: string;
  name: string;
  lineageId: string;
  description: string;
  algorithm: string;
  parameters: Record<string, unknown>;
  config: Record<string, unknown>;
  version: number;
  status: AnalysisTemplateStatus;
  useCaseId?: string | null;
  estimatedRuntimeSeconds?: number | null;
  supersededBy?: string | null;
  approvedBy?: string | null;
  approvedAt?: string | null;
  createdAt?: string;
  metadata: Record<string, unknown>;
}

export interface CreateUseCaseInput {
  title: string;
  description?: string;
  useCaseType?: string;
  priority?: string;
}

export interface CreateAnalysisTemplateInput {
  name: string;
  algorithm: string;
  description?: string;
  parameters?: Record<string, unknown>;
  config?: Record<string, unknown>;
  useCaseId?: string | null;
}

export interface RawUseCase {
  use_case_id: string;
  workspace_id: string;
  title: string;
  description?: string;
  use_case_type?: string;
  priority?: string;
  status: UseCaseStatus;
  origin?: string;
  requirement_version_id?: string | null;
  related_requirements?: string[];
  graph_algorithms?: string[];
  data_needs?: string[];
  expected_outputs?: string[];
  success_metrics?: string[];
  reviewed_by?: string | null;
  reviewed_at?: string | null;
  review_note?: string;
  created_at?: string;
  created_by?: string | null;
}

export interface RawAnalysisTemplate {
  analysis_template_id: string;
  workspace_id: string;
  name: string;
  lineage_id?: string;
  description?: string;
  algorithm?: string;
  parameters?: Record<string, unknown>;
  config?: Record<string, unknown>;
  version?: number;
  status: AnalysisTemplateStatus;
  use_case_id?: string | null;
  estimated_runtime_seconds?: number | null;
  superseded_by?: string | null;
  approved_by?: string | null;
  approved_at?: string | null;
  created_at?: string;
  metadata?: Record<string, unknown>;
}

export interface AnalysisExecution {
  analysisExecutionId: string;
  workspaceId: string;
  runId?: string | null;
  algorithm: string;
  status: string;
  graphProfileId?: string | null;
  requirementVersionId?: string | null;
  useCaseId?: string | null;
  templateId?: string | null;
  templateName: string;
  epochId?: string | null;
  algorithmVersion: string;
  parameters: Record<string, unknown>;
  resultsLocation?: string | null;
  resultCount: number;
  performanceMetrics: Record<string, number>;
  errorMessage?: string | null;
  workflowMode?: string | null;
  startedAt?: string | null;
  completedAt?: string | null;
  metadata: Record<string, unknown>;
}

export interface AnalysisEpoch {
  analysisEpochId: string;
  workspaceId: string;
  name: string;
  description: string;
  timestamp?: string | null;
  status: string;
  tags: string[];
  analysisCount: number;
  analysisExecutionIds: string[];
}

export interface AnalysisCatalogView {
  workspaceId: string;
  epochs: AnalysisEpoch[];
  executions: AnalysisExecution[];
  templates: AnalysisTemplate[];
  useCases: UseCase[];
  requirements: Array<Record<string, unknown>>;
  /** Execution references with no product record (runs that predate
   * FR-19..FR-26). Surfaced rather than dropped so lineage gaps stay visible. */
  unresolvedTemplateIds: string[];
  unresolvedUseCaseIds: string[];
}

/** FR-46 filters. All optional; omitted keys are not sent. */
export interface AnalysisExecutionFilters {
  algorithm?: string;
  status?: string;
  epochId?: string;
  graphProfileId?: string;
  startedAfter?: string;
  startedBefore?: string;
}

export interface AnalysisExecutionDelta {
  analysisExecutionId: string;
  resultCount: number;
  performanceMetrics: Record<string, number>;
}

export interface AnalysisExecutionComparison {
  workspaceId: string;
  /** Deltas are relative to this execution (the first one selected). */
  baselineExecutionId: string;
  executions: AnalysisExecution[];
  deltas: AnalysisExecutionDelta[];
}

export interface AnalysisLineage {
  workspaceId: string;
  execution: AnalysisExecution | null;
  reports: Array<Record<string, unknown>>;
  templateId?: string | null;
  useCaseId?: string | null;
  requirementVersionId?: string | null;
}

export interface RawAnalysisExecution {
  analysis_execution_id: string;
  workspace_id: string;
  run_id?: string | null;
  algorithm: string;
  status: string;
  graph_profile_id?: string | null;
  requirement_version_id?: string | null;
  use_case_id?: string | null;
  template_id?: string | null;
  template_name?: string;
  epoch_id?: string | null;
  algorithm_version?: string;
  parameters?: Record<string, unknown>;
  results_location?: string | null;
  result_count?: number;
  performance_metrics?: Record<string, number>;
  error_message?: string | null;
  workflow_mode?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  metadata?: Record<string, unknown>;
}

export interface RawAnalysisEpoch {
  analysis_epoch_id: string;
  workspace_id: string;
  name: string;
  description?: string;
  timestamp?: string | null;
  status: string;
  tags?: string[];
  analysis_count?: number;
  analysis_execution_ids?: string[];
}

export interface RawAnalysisCatalogView {
  workspace_id: string;
  epochs?: RawAnalysisEpoch[];
  executions?: RawAnalysisExecution[];
  templates?: RawAnalysisTemplate[];
  use_cases?: RawUseCase[];
  requirements?: Array<Record<string, unknown>>;
  unresolved_template_ids?: string[];
  unresolved_use_case_ids?: string[];
}

export interface RawAnalysisExecutionComparison {
  workspace_id: string;
  baseline_execution_id: string;
  executions?: RawAnalysisExecution[];
  deltas?: Array<{
    analysis_execution_id: string;
    result_count?: number;
    performance_metrics?: Record<string, number>;
  }>;
}

export interface RawAnalysisLineage {
  workspace_id: string;
  execution?: RawAnalysisExecution | null;
  reports?: Array<Record<string, unknown>>;
  template_id?: string | null;
  use_case_id?: string | null;
  requirement_version_id?: string | null;
}

/** FR-13: document content is sent base64-encoded in a JSON body — this
 * product API is JSON-only and has no multipart handling. */
export interface UploadSourceDocumentInput {
  filename: string;
  mimeType: string;
  contentBase64: string;
}

export interface ConnectionProfileSummary {
  connectionProfileId: string;
  workspaceId: string;
  name: string;
  deploymentMode: string;
  endpoint: string;
  database: string;
  username: string;
  verifySsl: boolean;
  secretRefs: Record<string, Record<string, string>>;
  lastVerificationStatus: string;
  lastVerifiedAt?: string | null;
  metadata: Record<string, unknown>;
}

/** How a connection profile logs in (NFR-24). "password" resolves the
 * env-var named by `passwordSecretEnvVar` on the server; "platform" stores no
 * secret at all — on the Arango platform whoever uses the profile acts as
 * themselves, with their own permissions. */
export type ConnectionLogin = "password" | "platform";

export interface CreateConnectionProfileInput {
  name: string;
  deploymentMode: string;
  endpoint: string;
  database: string;
  username: string;
  verifySsl: boolean;
  passwordSecretEnvVar?: string;
  login?: ConnectionLogin;
}

/** Two-step connect, part 1: cluster-level credentials used to enumerate
 * the databases visible on the cluster (no database name required up front).
 * The password is referenced by env-var name; no plaintext secret is sent. */
export interface ListClusterDatabasesInput {
  endpoint: string;
  username: string;
  passwordSecretEnvVar: string;
  verifySsl?: boolean;
  includeSystem?: boolean;
}

export interface WorkflowRunDeletion {
  runId: string;
  workspaceId: string;
  deleted: boolean;
  /** Cascaded with the run: reports and executions have no meaning without it. */
  reportsDeleted: number;
  executionsDeleted: number;
}

export interface GraphProfileDeletion {
  graphProfileId: string;
  workspaceId: string;
  deleted: boolean;
}

export interface ConnectionProfileDeletion {
  connectionProfileId: string;
  workspaceId: string;
  deleted: boolean;
}

/** Zero-config connect: databases on the cluster the server is already
 * configured for, plus the non-secret context describing that connection. */
export interface DefaultClusterDatabasesResult {
  endpoint: string;
  databases: string[];
  username: string;
  verifySsl: boolean;
  deploymentMode: string;
  login: ConnectionLogin;
}

export interface ClusterDatabasesResult {
  endpoint: string;
  databases: string[];
}

/** Non-secret connection defaults from the deployment environment, used to
 * prefill the connection-profile form. `passwordSecretEnvVar` is the env-var
 * *name* the password is referenced by — never the password value. */
export interface ConnectionDefaults {
  endpoint: string;
  username: string;
  database: string;
  verifySsl: boolean;
  deploymentMode: string;
  passwordSecretEnvVar: string;
  login: ConnectionLogin;
}

export interface ConnectionVerificationResult {
  connectionProfileId: string;
  workspaceId: string;
  status: string;
  verifiedAt: string;
  endpoint: string;
  database: string;
  errorMessage?: string | null;
  /** FR-7: best-effort deployment-wide GAE reachability, independent of
   * this profile's DB verification result above. */
  gaeStatus?: { status: string; message?: string } | null;
}

export interface DiscoverGraphProfileInput {
  graphName?: string;
  sampleSize: number;
  maxSamplesPerCollection: number;
  verifySystem: boolean;
  /** FR-67b: when true, ignore graphName and create a database-scope
   * profile named "default" covering every collection in the database. */
  forceDatabaseScope?: boolean;
}

export interface GraphDiscoveryResult {
  graphProfile: GraphProfileSummary;
  schemaSummary: Record<string, unknown>;
}

export interface ConnectionGraphSummary {
  name: string;
  isSystem: boolean;
  vertexCollections: string[];
  edgeCollections: string[];
  orphanCollections: string[];
  edgeDefinitions: Array<Record<string, unknown>>;
  vertexCount?: number | null;
  edgeCount?: number | null;
}

export interface ConnectionGraphsResult {
  connectionProfileId: string;
  workspaceId: string;
  database: string;
  graphs: ConnectionGraphSummary[];
}

export interface StartRequirementsCopilotInput {
  domain?: string;
  createdBy?: string;
  /** When set, the new interview is pre-populated with answers synthesised from
   * the named prior RequirementVersion so the user is editing rather than
   * retyping. The new interview still produces a fresh RequirementVersion on
   * approve; the prior one is flipped to SUPERSEDED automatically. */
  basedOnVersionId?: string;
}

export interface RequirementInterview {
  requirementInterviewId: string;
  workspaceId: string;
  graphProfileId: string;
  status: string;
  domain?: string | null;
  questions: Array<Record<string, unknown>>;
  answers: Array<Record<string, unknown>>;
  schemaObservations: Record<string, unknown>;
  inferences: Array<Record<string, unknown>>;
  assumptions: Array<Record<string, unknown>>;
  draftBrd?: string | null;
  provenanceLabels: Array<Record<string, unknown>>;
  metadata?: Record<string, unknown>;
}

export interface RequirementsDraftResult {
  requirementInterview: RequirementInterview;
  draftBrd: string;
  provenanceLabels: Array<Record<string, unknown>>;
}

export interface RequirementVersion {
  requirementVersionId: string;
  workspaceId: string;
  version: number;
  status: string;
  requirementInterviewId?: string | null;
  /** Source documents this version was extracted from. Requirements are not
   * graph-scoped — only a Copilot interview carries a graph profile — so this
   * is what a version can honestly be attributed to. */
  documentIds: string[];
  summary: string;
  objectives: Array<Record<string, unknown>>;
  requirements: Array<Record<string, unknown>>;
  constraints: Array<Record<string, unknown>>;
  approvedAt?: string | null;
  metadata: Record<string, unknown>;
}

export interface WorkflowArtifactRef {
  type: string;
  id: string;
  label?: string;
}

export interface WorkflowDAGNode {
  id: string;
  label: string;
  status: WorkflowStepStatus;
  agentName?: string;
  artifactCount: number;
  warningCount: number;
  errorCount: number;
  /** FR-36: full step detail for the FloatingDetailPanel, beyond the
   * summary counts above. Optional so demo-mode nodes (which only
   * synthesize the summary fields) still satisfy the type. */
  startedAt?: string | null;
  completedAt?: string | null;
  durationMs?: number | null;
  retryCount?: number;
  checkpointId?: string | null;
  inputs?: Record<string, unknown>;
  outputs?: Record<string, unknown>;
  artifactRefs?: WorkflowArtifactRef[];
  warningMessages?: string[];
  errorMessages?: string[];
  cost?: Record<string, unknown>;
}

export interface WorkflowDAGEdge {
  id: string;
  from: string;
  to: string;
  label?: string;
}

export interface WorkflowDAGView {
  runId: string;
  workspaceId: string;
  status: string;
  workflowMode: string;
  nodes: WorkflowDAGNode[];
  edges: WorkflowDAGEdge[];
  warnings: string[];
  errors: string[];
}

export type WorkflowRecoveryActions = Record<string, string[]>;

export interface WorkflowRunSummary {
  runId: string;
  workspaceId: string;
  workflowMode: string;
  status: string;
  startedAt?: string | null;
  completedAt?: string | null;
}

export interface WorkflowStepUpdateResult {
  workflowRun: WorkflowRunSummary;
  dagView: WorkflowDAGView;
}

export interface CreateWorkflowRunInput {
  workflowMode: string;
  stepLabels: string[];
}

/**
 * FR-73 Quick Analysis: a single natural-language prompt against a graph
 * profile runs the agentic pipeline end to end (ephemeral requirement +
 * run artifacts). `workflowMode` defaults to "agentic" on the backend.
 */
export interface QuickAnalysisInput {
  graphProfileId: string;
  prompt: string;
  workflowMode?: string;
}

export interface CreateWorkflowRunResult {
  workflowRun: WorkflowRunSummary;
  dagView: WorkflowDAGView;
}

/**
 * FR-31a Phase 1 status snapshot returned by GET /api/runs/{run_id}/status.
 *
 * The UI polls this endpoint to learn the supervisor-side outcome
 * (e.g. ``cancelled`` after the orchestrator observes the cancel
 * token) and the run's executor_kind, so it can label rows produced
 * by the in-process Phase 1 executor distinctly from rows produced
 * by future durable executors (FR-31b+).
 */
export interface WorkflowRunStatusView {
  runId: string;
  workspaceId: string;
  workflowMode: string;
  status: string;
  startedAt: string | null;
  completedAt: string | null;
  /** "inprocess" for FR-31a Phase 1; future executors will set their own. */
  executorKind: string | null;
  /** Supervisor-side outcome string: pending | running | completed | cancelled | failed. */
  lastOutcome: string | null;
  errors: string[];
  supervisor: {
    supervised: boolean;
    outcome?: string;
    cancelRequested?: boolean;
  };
}

export interface WorkspaceOverview {
  workspace: {
    workspace_id: string;
    customer_name: string;
    project_name: string;
    environment: string;
    /** Optional fields that the backend always sends as part of
     * Workspace.to_dict() but were originally omitted from the typed
     * shape. The Edit/Archive flows need them so the overlays can
     * pre-fill current values without a separate ``GET /workspaces/{id}``
     * round trip. */
    description?: string;
    status?: string;
    tags?: string[];
    active_graph_profile_id?: string | null;
  };
  counts: Record<string, number>;
  latestConnectionProfiles: ConnectionProfileSummary[];
  latestGraphProfiles: GraphProfileSummary[];
  latestSourceDocuments: SourceDocumentSummary[];
  latestRequirementVersions: RequirementVersion[];
  latestWorkflowRuns: Array<{
    run_id: string;
    status: string;
    workflow_mode: string;
    /** The graph profile the run targeted; resolves to graph + database.
     * Returned by the API but previously absent from this type, so the UI
     * could not say which database a run or its reports were about. */
    graph_profile_id?: string | null;
  }>;
  latestReports: Array<{
    report_id: string;
    title: string;
    status: string;
    /** A report carries no graph profile of its own — its target is its run's. */
    run_id?: string | null;
  }>;
  latestAuditEvents: Array<Record<string, unknown>>;
}

export interface WorkspaceHealthIssue {
  severity: "info" | "warning" | "error";
  code: string;
  message: string;
  entityIds: string[];
}

export interface WorkspaceHealth {
  workspaceId: string;
  status: "healthy" | "needs_attention" | string;
  counts: Record<string, number>;
  issues: WorkspaceHealthIssue[];
}

export interface ReportManifest {
  reportId: string;
  workspaceId: string;
  runId: string;
  title: string;
  status: string;
  summary: string;
  version: number;
}

export interface ReportSection {
  sectionId: string;
  order: number;
  type: string;
  title: string;
  content: Record<string, unknown>;
  evidenceRefs: Array<Record<string, string>>;
}

export interface ChartSpec {
  chartId: string;
  title: string;
  chartType: string;
  dataSource: Record<string, unknown>;
  data: Record<string, unknown>;
  encoding: Record<string, unknown>;
}

export interface ReportBundle {
  manifest: ReportManifest;
  sections: ReportSection[];
  charts: ChartSpec[];
  snapshots: Array<Record<string, unknown>>;
}

/** Supported report export formats (PRD FR-42 / MVP acceptance #14). PDF and
 * JSON are PRD-named but deferred until use-case generation produces enough
 * content to make them meaningfully different from the HTML/Markdown
 * exports. */
export type ReportExportFormat = "html" | "markdown";

export interface ReportExportDownload {
  blob: Blob;
  filename: string;
  format: ReportExportFormat;
}

export interface WorkspaceBundle {
  schemaVersion: string;
  workspace: Record<string, unknown>;
  connectionProfiles: Array<Record<string, unknown>>;
  graphProfiles: Array<Record<string, unknown>>;
  sourceDocuments: Array<Record<string, unknown>>;
  requirementInterviews: Array<Record<string, unknown>>;
  requirementVersions: Array<Record<string, unknown>>;
  workflowRuns: Array<Record<string, unknown>>;
  reports: Array<Record<string, unknown>>;
  auditEvents: Array<Record<string, unknown>>;
}

export interface WorkspaceImportResult {
  workspaceId: string;
  counts: Record<string, number>;
}

export interface ProductAPIClient {
  createWorkspace(input: CreateWorkspaceInput): Promise<WorkspaceSummary>;
  listWorkspaces(): Promise<WorkspaceSummary[]>;
  updateWorkspace(
    workspaceId: string,
    input: UpdateWorkspaceInput
  ): Promise<WorkspaceSummary>;
  /** Soft-delete a workspace by flipping its status to ``archived``. The
   * actor argument is recorded on the audit event so administrative actions
   * remain attributable. */
  archiveWorkspace(workspaceId: string, actor?: string): Promise<WorkspaceSummary>;
  /** Set (or clear) the GraphProfile that drives the "Analyzing X" banner
   * and the default Requirements Copilot target. Pass null to clear and
   * fall back to the deterministic positional rule. The id must belong
   * to a profile in this workspace. */
  setActiveGraphProfile(
    workspaceId: string,
    graphProfileId: string | null,
    actor?: string
  ): Promise<WorkspaceSummary>;
  getWorkspaceOverview(workspaceId: string): Promise<WorkspaceOverview>;
  getWorkspaceHealth(workspaceId: string): Promise<WorkspaceHealth>;
  createConnectionProfile(
    workspaceId: string,
    input: CreateConnectionProfileInput
  ): Promise<ConnectionProfileSummary>;
  verifyConnectionProfile(connectionProfileId: string): Promise<ConnectionVerificationResult>;
  /** Two-step connect, part 1: list databases visible to cluster credentials. */
  listClusterDatabases(
    input: ListClusterDatabasesInput
  ): Promise<ClusterDatabasesResult>;
  /** Non-secret connection defaults from the environment, for form prefill. */
  getConnectionDefaults(): Promise<ConnectionDefaults>;
  /** FR-13: upload a source document; only extracted text is persisted. */
  uploadSourceDocument(
    workspaceId: string,
    input: UploadSourceDocumentInput
  ): Promise<SourceDocumentSummary>;
  /** FR-19: author a use case by hand. */
  createUseCase(workspaceId: string, input: CreateUseCaseInput): Promise<UseCase>;
  /** FR-20: approve / reject / archive a use case. */
  setUseCaseStatus(
    useCaseId: string,
    status: UseCaseStatus,
    reviewNote?: string
  ): Promise<UseCase>;
  /** FR-20: re-prioritise a use case at any non-archived status. */
  setUseCasePriority(useCaseId: string, priority: string): Promise<UseCase>;
  /** FR-22: create a draft analysis template. */
  createAnalysisTemplate(
    workspaceId: string,
    input: CreateAnalysisTemplateInput
  ): Promise<AnalysisTemplate>;
  /** FR-23: edit algorithm parameters; versions an approved template (FR-25). */
  updateAnalysisTemplate(
    analysisTemplateId: string,
    patch: { parameters?: Record<string, unknown>; config?: Record<string, unknown> }
  ): Promise<AnalysisTemplate>;
  /** FR-25: approve a draft template, making it immutable. */
  approveAnalysisTemplate(analysisTemplateId: string): Promise<AnalysisTemplate>;
  /** FR-25: every version in a template's lineage, oldest first. */
  getAnalysisTemplateVersions(
    analysisTemplateId: string
  ): Promise<AnalysisTemplate[]>;
  /** FR-26: import template dictionaries; nothing in the payload is executed. */
  importAnalysisTemplates(
    workspaceId: string,
    templates: Array<Record<string, unknown>>
  ): Promise<AnalysisTemplate[]>;
  /** FR-54: read the workspace retention policy. */
  getRetentionPolicy(workspaceId: string): Promise<RetentionPolicy>;
  /** FR-54: configure retention windows. */
  setRetentionPolicy(
    workspaceId: string,
    input: SetRetentionPolicyInput
  ): Promise<RetentionPolicy>;
  /** FR-54: sweep expired records. Dry run unless dryRun is false. */
  applyRetentionPolicy(
    workspaceId: string,
    dryRun?: boolean
  ): Promise<RetentionSweepResult>;
  /** FR-49/FR-50: import a vertical project bundle (YAML or JSON). */
  importVerticalProject(
    workspaceId: string,
    document: string,
    documentFormat?: string
  ): Promise<VerticalProjectImportResult>;
  /** FR-45: browse epochs + executions for a workspace. */
  browseAnalysisCatalog(workspaceId: string): Promise<AnalysisCatalogView>;
  /** FR-46: server-side filtered execution search. */
  listAnalysisExecutions(
    workspaceId: string,
    filters?: AnalysisExecutionFilters
  ): Promise<AnalysisExecution[]>;
  /** FR-48: compare executions; deltas are relative to the first id. */
  compareAnalysisExecutions(
    workspaceId: string,
    analysisExecutionIds: string[]
  ): Promise<AnalysisExecutionComparison>;
  /** FR-47: trace report -> execution -> template -> use case -> requirement. */
  getAnalysisLineage(analysisExecutionId: string): Promise<AnalysisLineage>;
  listConnectionProfileGraphs(
    connectionProfileId: string
  ): Promise<ConnectionGraphsResult>;
  discoverGraphProfile(
    connectionProfileId: string,
    input: DiscoverGraphProfileInput
  ): Promise<GraphDiscoveryResult>;
  startRequirementsCopilot(
    graphProfileId: string,
    input: StartRequirementsCopilotInput
  ): Promise<RequirementInterview>;
  answerRequirementsCopilotQuestion(
    requirementInterviewId: string,
    questionId: string,
    answer: string,
    actor?: string
  ): Promise<RequirementInterview>;
  generateRequirementsCopilotDraft(
    requirementInterviewId: string
  ): Promise<RequirementsDraftResult>;
  /** FR-15: promote a document's extracted requirements into a DRAFT version. */
  promoteExtractedRequirements(
    documentId: string,
    actor?: string
  ): Promise<RequirementVersion>;
  /** FR-15: approve a DRAFT requirement version, superseding the prior set. */
  approveRequirementVersion(
    requirementVersionId: string,
    approvedBy?: string
  ): Promise<RequirementVersion>;
  approveRequirementsCopilotDraft(
    requirementInterviewId: string,
    /** Pass `null` (recommended) to auto-increment to max(existing.version)+1.
     * Passing a specific number that collides with an existing version raises
     * a validation error from the backend. */
    version: number | null,
    approvedBy?: string
  ): Promise<RequirementVersion>;
  getWorkflowDAG(runId: string): Promise<WorkflowDAGView>;
  getReportBundle(reportId: string): Promise<ReportBundle>;
  publishReport(reportId: string, actor: string): Promise<ReportBundle>;
  exportReport(
    reportId: string,
    format: ReportExportFormat
  ): Promise<ReportExportDownload>;
  exportWorkspaceBundle(workspaceId: string): Promise<WorkspaceBundle>;
  importWorkspaceBundle(bundle: WorkspaceBundle): Promise<WorkspaceImportResult>;
  getWorkflowRecoveryActions(runId: string): Promise<WorkflowRecoveryActions>;
  createWorkflowRun(
    workspaceId: string,
    input: CreateWorkflowRunInput
  ): Promise<CreateWorkflowRunResult>;
  /** FR-73: one-shot analysis from a prompt. Creates an ephemeral
   * requirement version + agentic run and starts it, returning the
   * started run + its DAG view. */
  quickAnalysis(
    workspaceId: string,
    input: QuickAnalysisInput
  ): Promise<CreateWorkflowRunResult>;
  startWorkflowRun(runId: string): Promise<WorkflowRunSummary>;
  /** FR-31a: cooperative cancel of a running agentic workflow. */
  cancelWorkflowRun(runId: string, actor?: string): Promise<WorkflowRunSummary>;
  /** FR-31a: lightweight status poll (supervisor + executor metadata). */
  listDefaultClusterDatabases(): Promise<DefaultClusterDatabasesResult>;
  deleteConnectionProfile(
    connectionProfileId: string
  ): Promise<ConnectionProfileDeletion>;
  deleteGraphProfile(graphProfileId: string): Promise<GraphProfileDeletion>;
  deleteWorkflowRun(runId: string): Promise<WorkflowRunDeletion>;
  getWorkflowRunStatus(runId: string): Promise<WorkflowRunStatusView>;
  updateWorkflowStep(
    runId: string,
    stepId: string,
    status: WorkflowStepStatus
  ): Promise<WorkflowStepUpdateResult>;
}

export interface RawWorkspaceOverview {
  workspace: WorkspaceOverview["workspace"];
  counts: Record<string, number>;
  latest_connection_profiles?: RawConnectionProfileSummary[];
  latest_graph_profiles?: RawGraphProfileSummary[];
  latest_source_documents?: RawSourceDocumentSummary[];
  latest_requirement_versions?: RawRequirementVersion[];
  latest_workflow_runs: WorkspaceOverview["latestWorkflowRuns"];
  latest_reports: WorkspaceOverview["latestReports"];
  latest_audit_events?: Array<Record<string, unknown>>;
}

export interface RawWorkspaceSummary {
  workspace_id: string;
  customer_name: string;
  project_name: string;
  environment: string;
  description?: string;
  status?: string;
  tags?: string[];
  active_graph_profile_id?: string | null;
}

export interface RawConnectionProfileSummary {
  connection_profile_id: string;
  workspace_id: string;
  name: string;
  deployment_mode: string;
  endpoint: string;
  database: string;
  username: string;
  verify_ssl?: boolean;
  secret_refs?: Record<string, Record<string, string>>;
  last_verification_status?: string;
  last_verified_at?: string | null;
  metadata?: Record<string, unknown>;
}

export interface RawConnectionVerificationResult {
  connection_profile_id: string;
  workspace_id: string;
  status: string;
  verified_at: string;
  endpoint: string;
  database: string;
  error_message?: string | null;
  gae_status?: { status: string; message?: string } | null;
}

export interface RawGraphDiscoveryResult {
  graph_profile: RawGraphProfileSummary;
  schema_summary: Record<string, unknown>;
}

export interface RawRequirementInterview {
  requirement_interview_id: string;
  workspace_id: string;
  graph_profile_id: string;
  status: string;
  domain?: string | null;
  questions?: Array<Record<string, unknown>>;
  answers?: Array<Record<string, unknown>>;
  schema_observations?: Record<string, unknown>;
  inferences?: Array<Record<string, unknown>>;
  assumptions?: Array<Record<string, unknown>>;
  draft_brd?: string | null;
  provenance_labels?: Array<Record<string, unknown>>;
  metadata?: Record<string, unknown>;
}

export interface RawRequirementsDraftResult {
  requirement_interview: RawRequirementInterview;
  draft_brd: string;
  provenance_labels?: Array<Record<string, unknown>>;
}

export interface RawRequirementVersion {
  requirement_version_id: string;
  workspace_id: string;
  version: number;
  status: string;
  document_ids?: string[] | null;
  requirement_interview_id?: string | null;
  summary?: string;
  objectives?: Array<Record<string, unknown>>;
  requirements?: Array<Record<string, unknown>>;
  constraints?: Array<Record<string, unknown>>;
  approved_at?: string | null;
  metadata?: Record<string, unknown>;
}

export interface RawSourceDocumentSummary {
  document_id: string;
  workspace_id: string;
  filename: string;
  mime_type: string;
  sha256?: string;
  storage_mode?: string;
  storage_uri?: string | null;
  extracted_text?: string | null;
  uploaded_at?: string;
  metadata?: Record<string, unknown>;
}

export interface RawGraphProfileSummary {
  graph_profile_id: string;
  workspace_id: string;
  connection_profile_id: string;
  graph_name: string;
  status: string;
  version?: number;
  vertex_collections?: string[];
  edge_collections?: string[];
  edge_definitions?: Array<Record<string, unknown>>;
  collection_roles?: Record<string, string[]>;
  counts?: Record<string, number>;
  analyzer_metadata?: { sharding_profile?: RawShardingProfile } & Record<
    string,
    unknown
  >;
}

export interface RawWorkflowDAGNode {
  id: string;
  label: string;
  status: WorkflowStepStatus;
  agent_name?: string;
  started_at?: string | null;
  completed_at?: string | null;
  duration_ms?: number | null;
  retry_count?: number;
  checkpoint_id?: string | null;
  inputs?: Record<string, unknown>;
  outputs?: Record<string, unknown>;
  artifact_refs?: Array<{ type: string; id: string; label?: string }>;
  warnings?: string[];
  errors?: string[];
  cost?: Record<string, unknown>;
}

export interface RawWorkflowDAGView {
  run_id: string;
  workspace_id: string;
  status: string;
  workflow_mode: string;
  nodes: RawWorkflowDAGNode[];
  edges: Array<{
    id: string;
    from: string;
    to: string;
    label?: string;
  }>;
  warnings: string[];
  errors: string[];
}

export interface RawWorkflowRunSummary {
  run_id: string;
  workspace_id: string;
  workflow_mode: string;
  status: string;
  started_at?: string | null;
  completed_at?: string | null;
  steps?: Array<{
    step_id: string;
    label: string;
    status: WorkflowStepStatus;
    agent_name?: string;
    started_at?: string | null;
    completed_at?: string | null;
    duration_ms?: number | null;
    retry_count?: number;
    checkpoint_id?: string | null;
    inputs?: Record<string, unknown>;
    outputs?: Record<string, unknown>;
    artifact_refs?: Array<{ type: string; id: string; label?: string }>;
    warnings?: string[];
    errors?: string[];
    cost?: Record<string, unknown>;
  }>;
  dag_edges?: Array<{
    from_step_id: string;
    to_step_id: string;
    label?: string;
  }>;
  warnings?: string[];
  errors?: string[];
}

export interface RawWorkflowStepUpdateResult {
  workflow_run: RawWorkflowRunSummary;
  dag_view: RawWorkflowDAGView;
}

export interface RawWorkspaceHealth {
  workspace_id: string;
  status: string;
  counts: Record<string, number>;
  issues: Array<{
    severity: "info" | "warning" | "error";
    code: string;
    message: string;
    entity_ids?: string[];
  }>;
}

export interface RawReportBundle {
  manifest: {
    report_id: string;
    workspace_id: string;
    run_id: string;
    title: string;
    status: string;
    summary?: string;
    version?: number;
  };
  sections: Array<{
    section_id: string;
    order: number;
    type: string;
    title: string;
    content?: Record<string, unknown>;
    evidence_refs?: Array<Record<string, string>>;
  }>;
  charts: Array<{
    chart_id: string;
    title: string;
    chart_type: string;
    data_source?: Record<string, unknown>;
    data?: Record<string, unknown>;
    encoding?: Record<string, unknown>;
  }>;
  snapshots: Array<Record<string, unknown>>;
}

export interface RawWorkspaceBundle {
  schema_version: string;
  workspace: Record<string, unknown>;
  connection_profiles?: Array<Record<string, unknown>>;
  graph_profiles?: Array<Record<string, unknown>>;
  source_documents?: Array<Record<string, unknown>>;
  requirement_interviews?: Array<Record<string, unknown>>;
  requirement_versions?: Array<Record<string, unknown>>;
  workflow_runs?: Array<Record<string, unknown>>;
  reports?: Array<Record<string, unknown>>;
  audit_events?: Array<Record<string, unknown>>;
}

export interface RawWorkspaceImportResult {
  workspace_id: string;
  counts: Record<string, number>;
}

export interface RawConnectionGraphSummary {
  name: string;
  is_system?: boolean;
  vertex_collections?: string[];
  edge_collections?: string[];
  orphan_collections?: string[];
  edge_definitions?: Array<Record<string, unknown>>;
  vertex_count?: number | null;
  edge_count?: number | null;
}

export interface RawConnectionGraphsResult {
  connection_profile_id: string;
  workspace_id: string;
  database: string;
  graphs: RawConnectionGraphSummary[];
}
