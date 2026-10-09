import type {
  ChartSpec,
  ClusterDatabasesResult,
  ConnectionProfileDeletion,
  GraphProfileDeletion,
  WorkflowRunDeletion,
  DefaultClusterDatabasesResult,
  ConnectionDefaults,
  ConnectionLogin,
  ConnectionGraphSummary,
  ConnectionGraphsResult,
  ConnectionProfileSummary,
  ConnectionVerificationResult,
  CreateConnectionProfileInput,
  CreateWorkspaceInput,
  CreateWorkflowRunInput,
  CreateWorkflowRunResult,
  DiscoverGraphProfileInput,
  GraphDiscoveryResult,
  GraphProfileSummary,
  ListClusterDatabasesInput,
  ProductAPIClient,
  QuickAnalysisInput,
  RawConnectionGraphSummary,
  RawConnectionGraphsResult,
  RawConnectionProfileSummary,
  RawConnectionVerificationResult,
  RawGraphDiscoveryResult,
  RawGraphProfileSummary,
  RawRequirementInterview,
  RawRequirementVersion,
  RawRequirementsDraftResult,
  RawReportBundle,
  AnalysisCatalogView,
  AnalysisEpoch,
  AnalysisTemplate,
  AnalysisExecution,
  AnalysisExecutionComparison,
  AnalysisExecutionFilters,
  AnalysisLineage,
  CreateAnalysisTemplateInput,
  CreateUseCaseInput,
  RawAnalysisCatalogView,
  RawAnalysisEpoch,
  RawAnalysisTemplate,
  RawUseCase,
  RawAnalysisExecution,
  RawAnalysisExecutionComparison,
  RawAnalysisLineage,
  RawSourceDocumentSummary,
  RawWorkflowDAGView,
  RawWorkflowRunSummary,
  RawWorkflowStepUpdateResult,
  RawWorkspaceBundle,
  RawWorkspaceHealth,
  RawWorkspaceImportResult,
  RawWorkspaceOverview,
  RawWorkspaceSummary,
  ReportBundle,
  ReportExportDownload,
  ReportExportFormat,
  ReportSection,
  RequirementInterview,
  RequirementVersion,
  RequirementsDraftResult,
  SourceDocumentSummary,
  UseCase,
  UseCaseStatus,
  RetentionPolicy,
  RetentionSweepResult,
  SetRetentionPolicyInput,
  ShardingProfile,
  VerticalProjectImportResult,
  RawRetentionPolicy,
  RawRetentionSweepResult,
  RawShardingProfile,
  RawVerticalProjectImportResult,
  StartRequirementsCopilotInput,
  UpdateWorkspaceInput,
  UploadSourceDocumentInput,
  WorkflowDAGEdge,
  WorkflowDAGNode,
  WorkflowDAGView,
  WorkflowRecoveryActions,
  WorkflowRunStatusView,
  WorkflowRunSummary,
  WorkflowStepStatus,
  WorkflowStepUpdateResult,
  WorkflowArtifactRef,
  WorkspaceAsset,
  WorkspaceBundle,
  WorkspaceHealth,
  WorkspaceImportResult,
  WorkspaceOverview,
  WorkspaceSummary
} from "./types";

const DEFAULT_API_BASE_URL = "http://127.0.0.1:8000";

export function createProductAPIClient(
  baseUrl = process.env.NEXT_PUBLIC_PRODUCT_API_BASE_URL ?? DEFAULT_API_BASE_URL
): ProductAPIClient {
  const normalizedBaseUrl = baseUrl.replace(/\/$/, "");

  return {
    async createWorkspace(input: CreateWorkspaceInput): Promise<WorkspaceSummary> {
      return mapWorkspaceSummary(
        await postJSON<RawWorkspaceSummary>(
          `${normalizedBaseUrl}/api/workspaces`,
          createWorkspacePayload(input)
        )
      );
    },
    async listWorkspaces(): Promise<WorkspaceSummary[]> {
      const raw = await getJSON<RawWorkspaceSummary[]>(
        `${normalizedBaseUrl}/api/workspaces`
      );
      return Array.isArray(raw) ? raw.map(mapWorkspaceSummary) : [];
    },
    async updateWorkspace(
      workspaceId: string,
      input: UpdateWorkspaceInput
    ): Promise<WorkspaceSummary> {
      // Only forward fields the caller actually set so the backend can tell
      // a "patch this field to empty" from a "leave it alone" without
      // additional sentinels. Sending the full payload every time would
      // push the diff cost onto the backend.
      const payload: Record<string, unknown> = {};
      if (input.customerName !== undefined) payload.customer_name = input.customerName;
      if (input.projectName !== undefined) payload.project_name = input.projectName;
      if (input.environment !== undefined) payload.environment = input.environment;
      if (input.description !== undefined) payload.description = input.description;
      if (input.tags !== undefined) payload.tags = input.tags;
      if (input.actor !== undefined) payload.actor = input.actor;

      return mapWorkspaceSummary(
        await patchJSON<RawWorkspaceSummary>(
          `${normalizedBaseUrl}/api/workspaces/${workspaceId}`,
          payload
        )
      );
    },
    async setActiveGraphProfile(
      workspaceId: string,
      graphProfileId: string | null,
      actor = "workspace-ui"
    ): Promise<WorkspaceSummary> {
      const payload: Record<string, unknown> = {
        graph_profile_id: graphProfileId,
        actor
      };
      return mapWorkspaceSummary(
        await patchJSON<RawWorkspaceSummary>(
          `${normalizedBaseUrl}/api/workspaces/${workspaceId}/active-graph-profile`,
          payload
        )
      );
    },
    async archiveWorkspace(
      workspaceId: string,
      actor = "workspace-ui"
    ): Promise<WorkspaceSummary> {
      return mapWorkspaceSummary(
        await postJSON<RawWorkspaceSummary>(
          `${normalizedBaseUrl}/api/workspaces/${workspaceId}/archive`,
          { actor }
        )
      );
    },
    async getWorkspaceOverview(workspaceId: string): Promise<WorkspaceOverview> {
      return mapWorkspaceOverview(
        await getJSON<RawWorkspaceOverview>(
          `${normalizedBaseUrl}/api/workspaces/${workspaceId}/overview`
        )
      );
    },
    async getWorkspaceHealth(workspaceId: string): Promise<WorkspaceHealth> {
      return mapWorkspaceHealth(
        await getJSON<RawWorkspaceHealth>(
          `${normalizedBaseUrl}/api/workspaces/${workspaceId}/health`
        )
      );
    },
    async createConnectionProfile(
      workspaceId: string,
      input: CreateConnectionProfileInput
    ): Promise<ConnectionProfileSummary> {
      return mapConnectionProfileSummary(
        await postJSON<RawConnectionProfileSummary>(
          `${normalizedBaseUrl}/api/workspaces/${workspaceId}/connection-profiles`,
          createConnectionProfilePayload(input)
        )
      );
    },
    async verifyConnectionProfile(
      connectionProfileId: string
    ): Promise<ConnectionVerificationResult> {
      return mapConnectionVerificationResult(
        await postJSON<RawConnectionVerificationResult>(
          `${normalizedBaseUrl}/api/connection-profiles/${connectionProfileId}/verify`,
          {}
        )
      );
    },
    async uploadSourceDocument(
      workspaceId: string,
      input: UploadSourceDocumentInput
    ): Promise<SourceDocumentSummary> {
      return mapSourceDocumentSummary(
        await postJSON<RawSourceDocumentSummary>(
          `${normalizedBaseUrl}/api/workspaces/${workspaceId}/documents`,
          {
            filename: input.filename,
            mime_type: input.mimeType,
            content_base64: input.contentBase64
          }
        )
      );
    },
    async createUseCase(
      workspaceId: string,
      input: CreateUseCaseInput
    ): Promise<UseCase> {
      return mapUseCase(
        await postJSON<RawUseCase>(
          `${normalizedBaseUrl}/api/workspaces/${workspaceId}/use-cases`,
          {
            title: input.title,
            description: input.description ?? "",
            use_case_type: input.useCaseType ?? "pattern",
            priority: input.priority ?? "medium"
          }
        )
      );
    },
    async setUseCaseStatus(
      useCaseId: string,
      status: UseCaseStatus,
      reviewNote = ""
    ): Promise<UseCase> {
      return mapUseCase(
        await postJSON<RawUseCase>(
          `${normalizedBaseUrl}/api/use-cases/${useCaseId}/status`,
          { status, review_note: reviewNote }
        )
      );
    },
    async setUseCasePriority(useCaseId: string, priority: string): Promise<UseCase> {
      return mapUseCase(
        await postJSON<RawUseCase>(
          `${normalizedBaseUrl}/api/use-cases/${useCaseId}/priority`,
          { priority }
        )
      );
    },
    async createAnalysisTemplate(
      workspaceId: string,
      input: CreateAnalysisTemplateInput
    ): Promise<AnalysisTemplate> {
      return mapAnalysisTemplate(
        await postJSON<RawAnalysisTemplate>(
          `${normalizedBaseUrl}/api/workspaces/${workspaceId}/analysis-templates`,
          {
            name: input.name,
            algorithm: input.algorithm,
            description: input.description ?? "",
            parameters: input.parameters ?? {},
            config: input.config ?? {},
            ...(input.useCaseId ? { use_case_id: input.useCaseId } : {})
          }
        )
      );
    },
    async updateAnalysisTemplate(
      analysisTemplateId: string,
      patch: { parameters?: Record<string, unknown>; config?: Record<string, unknown> }
    ): Promise<AnalysisTemplate> {
      return mapAnalysisTemplate(
        await patchJSON<RawAnalysisTemplate>(
          `${normalizedBaseUrl}/api/analysis-templates/${analysisTemplateId}`,
          patch
        )
      );
    },
    async approveAnalysisTemplate(
      analysisTemplateId: string
    ): Promise<AnalysisTemplate> {
      return mapAnalysisTemplate(
        await postJSON<RawAnalysisTemplate>(
          `${normalizedBaseUrl}/api/analysis-templates/${analysisTemplateId}/approve`,
          {}
        )
      );
    },
    async getAnalysisTemplateVersions(
      analysisTemplateId: string
    ): Promise<AnalysisTemplate[]> {
      const raw = await getJSON<RawAnalysisTemplate[]>(
        `${normalizedBaseUrl}/api/analysis-templates/${analysisTemplateId}/versions`
      );
      return (raw ?? []).map(mapAnalysisTemplate);
    },
    async importAnalysisTemplates(
      workspaceId: string,
      templates: Array<Record<string, unknown>>
    ): Promise<AnalysisTemplate[]> {
      const raw = await postJSON<RawAnalysisTemplate[]>(
        `${normalizedBaseUrl}/api/workspaces/${workspaceId}/analysis-templates/import`,
        { templates }
      );
      return (raw ?? []).map(mapAnalysisTemplate);
    },
    async getRetentionPolicy(workspaceId: string): Promise<RetentionPolicy> {
      return mapRetentionPolicy(
        await getJSON<RawRetentionPolicy>(
          `${normalizedBaseUrl}/api/workspaces/${workspaceId}/retention-policy`
        )
      );
    },
    async setRetentionPolicy(
      workspaceId: string,
      input: SetRetentionPolicyInput
    ): Promise<RetentionPolicy> {
      // Only send fields the admin actually set, so a partial edit cannot
      // silently reset the other windows to 0.
      const payload: Record<string, unknown> = {};
      if (input.enabled !== undefined) payload.enabled = input.enabled;
      if (input.draftRetentionDays !== undefined)
        payload.draft_retention_days = input.draftRetentionDays;
      if (input.runRetentionDays !== undefined)
        payload.run_retention_days = input.runRetentionDays;
      if (input.documentRetentionDays !== undefined)
        payload.document_retention_days = input.documentRetentionDays;
      if (input.reportSnapshotRetentionDays !== undefined)
        payload.report_snapshot_retention_days = input.reportSnapshotRetentionDays;
      if (input.auditLogRetentionDays !== undefined)
        payload.audit_log_retention_days = input.auditLogRetentionDays;

      return mapRetentionPolicy(
        await putJSON<RawRetentionPolicy>(
          `${normalizedBaseUrl}/api/workspaces/${workspaceId}/retention-policy`,
          payload
        )
      );
    },
    async applyRetentionPolicy(
      workspaceId: string,
      dryRun = true
    ): Promise<RetentionSweepResult> {
      return mapRetentionSweepResult(
        await postJSON<RawRetentionSweepResult>(
          `${normalizedBaseUrl}/api/workspaces/${workspaceId}/retention-policy/apply`,
          { dry_run: dryRun }
        )
      );
    },
    async importVerticalProject(
      workspaceId: string,
      document: string,
      documentFormat = "yaml"
    ): Promise<VerticalProjectImportResult> {
      return mapVerticalProjectImportResult(
        await postJSON<RawVerticalProjectImportResult>(
          `${normalizedBaseUrl}/api/workspaces/${workspaceId}/vertical-projects/import`,
          { document, document_format: documentFormat }
        )
      );
    },
    async browseAnalysisCatalog(workspaceId: string): Promise<AnalysisCatalogView> {
      return mapAnalysisCatalogView(
        await getJSON<RawAnalysisCatalogView>(
          `${normalizedBaseUrl}/api/workspaces/${workspaceId}/analysis-catalog`
        )
      );
    },
    async listAnalysisExecutions(
      workspaceId: string,
      filters: AnalysisExecutionFilters = {}
    ): Promise<AnalysisExecution[]> {
      // Only send filters the user actually set — the backend treats a
      // present-but-empty filter as a real constraint and would match nothing.
      const query = new URLSearchParams();
      const wireNames: Array<[keyof AnalysisExecutionFilters, string]> = [
        ["algorithm", "algorithm"],
        ["status", "status"],
        ["epochId", "epoch_id"],
        ["graphProfileId", "graph_profile_id"],
        ["startedAfter", "started_after"],
        ["startedBefore", "started_before"]
      ];
      for (const [key, wireName] of wireNames) {
        const value = filters[key];
        if (value !== undefined && value !== null && value !== "") {
          query.set(wireName, value);
        }
      }
      const suffix = query.toString() ? `?${query.toString()}` : "";
      const raw = await getJSON<RawAnalysisExecution[]>(
        `${normalizedBaseUrl}/api/workspaces/${workspaceId}/analysis-executions${suffix}`
      );
      return (raw ?? []).map(mapAnalysisExecution);
    },
    async compareAnalysisExecutions(
      workspaceId: string,
      analysisExecutionIds: string[]
    ): Promise<AnalysisExecutionComparison> {
      return mapAnalysisExecutionComparison(
        await postJSON<RawAnalysisExecutionComparison>(
          `${normalizedBaseUrl}/api/workspaces/${workspaceId}/analysis-executions/compare`,
          { analysis_execution_ids: analysisExecutionIds }
        )
      );
    },
    async getAnalysisLineage(analysisExecutionId: string): Promise<AnalysisLineage> {
      return mapAnalysisLineage(
        await getJSON<RawAnalysisLineage>(
          `${normalizedBaseUrl}/api/analysis-executions/${analysisExecutionId}/lineage`
        )
      );
    },
    async listClusterDatabases(
      input: ListClusterDatabasesInput
    ): Promise<ClusterDatabasesResult> {
      const raw = await postJSON<{ endpoint: string; databases?: string[] }>(
        `${normalizedBaseUrl}/api/connections/list-databases`,
        {
          endpoint: input.endpoint,
          username: input.username,
          password_secret_env_var: input.passwordSecretEnvVar,
          verify_ssl: input.verifySsl ?? true,
          ...(input.includeSystem ? { include_system: true } : {})
        }
      );
      return { endpoint: raw.endpoint, databases: raw.databases ?? [] };
    },
    async deleteWorkflowRun(runId: string): Promise<WorkflowRunDeletion> {
      const raw = await deleteJSON<{
        run_id: string;
        workspace_id: string;
        deleted?: boolean;
        reports_deleted?: number;
        executions_deleted?: number;
      }>(`${normalizedBaseUrl}/api/runs/${encodeURIComponent(runId)}`);
      return {
        runId: raw.run_id,
        workspaceId: raw.workspace_id,
        deleted: raw.deleted ?? true,
        reportsDeleted: raw.reports_deleted ?? 0,
        executionsDeleted: raw.executions_deleted ?? 0
      };
    },
    async deleteGraphProfile(
      graphProfileId: string
    ): Promise<GraphProfileDeletion> {
      const raw = await deleteJSON<{
        graph_profile_id: string;
        workspace_id: string;
        deleted?: boolean;
      }>(
        `${normalizedBaseUrl}/api/graph-profiles/${encodeURIComponent(graphProfileId)}`
      );
      return {
        graphProfileId: raw.graph_profile_id,
        workspaceId: raw.workspace_id,
        deleted: raw.deleted ?? true
      };
    },
    async deleteConnectionProfile(
      connectionProfileId: string
    ): Promise<ConnectionProfileDeletion> {
      const raw = await deleteJSON<{
        connection_profile_id: string;
        workspace_id: string;
        deleted?: boolean;
      }>(
        `${normalizedBaseUrl}/api/connection-profiles/${encodeURIComponent(
          connectionProfileId
        )}`
      );
      return {
        connectionProfileId: raw.connection_profile_id,
        workspaceId: raw.workspace_id,
        deleted: raw.deleted ?? true
      };
    },
    async listDefaultClusterDatabases(): Promise<DefaultClusterDatabasesResult> {
      // No credentials in the payload: the server uses its own environment.
      const raw = await postJSON<{
        endpoint: string;
        databases?: string[];
        username?: string;
        verify_ssl?: boolean;
        deployment_mode?: string;
        login?: string;
      }>(`${normalizedBaseUrl}/api/connections/default-cluster/databases`, {});
      return {
        endpoint: raw.endpoint,
        databases: raw.databases ?? [],
        username: raw.username ?? "",
        verifySsl: raw.verify_ssl ?? true,
        deploymentMode: raw.deployment_mode ?? "",
        login: mapConnectionLogin(raw.login)
      };
    },
    async getConnectionDefaults(): Promise<ConnectionDefaults> {
      const raw = await getJSON<{
        endpoint?: string;
        username?: string;
        database?: string;
        verify_ssl?: boolean;
        deployment_mode?: string;
        password_secret_env_var?: string;
        login?: string;
      }>(`${normalizedBaseUrl}/api/connections/defaults`);
      return {
        endpoint: raw.endpoint ?? "",
        username: raw.username ?? "",
        database: raw.database ?? "",
        verifySsl: raw.verify_ssl ?? true,
        deploymentMode: raw.deployment_mode ?? "",
        passwordSecretEnvVar: raw.password_secret_env_var ?? "ARANGO_PASSWORD",
        login: mapConnectionLogin(raw.login)
      };
    },
    async listConnectionProfileGraphs(
      connectionProfileId: string
    ): Promise<ConnectionGraphsResult> {
      return mapConnectionGraphsResult(
        await getJSON<RawConnectionGraphsResult>(
          `${normalizedBaseUrl}/api/connection-profiles/${connectionProfileId}/graphs`
        )
      );
    },
    async discoverGraphProfile(
      connectionProfileId: string,
      input: DiscoverGraphProfileInput
    ): Promise<GraphDiscoveryResult> {
      return mapGraphDiscoveryResult(
        await postJSON<RawGraphDiscoveryResult>(
          `${normalizedBaseUrl}/api/connection-profiles/${connectionProfileId}/discover-graph`,
          discoverGraphProfilePayload(input)
        )
      );
    },
    async startRequirementsCopilot(
      graphProfileId: string,
      input: StartRequirementsCopilotInput
    ): Promise<RequirementInterview> {
      return mapRequirementInterview(
        await postJSON<RawRequirementInterview>(
          `${normalizedBaseUrl}/api/graph-profiles/${graphProfileId}/requirements-copilot/sessions`,
          startRequirementsCopilotPayload(input)
        )
      );
    },
    async answerRequirementsCopilotQuestion(
      requirementInterviewId: string,
      questionId: string,
      answer: string,
      actor = "workspace-ui"
    ): Promise<RequirementInterview> {
      return mapRequirementInterview(
        await postJSON<RawRequirementInterview>(
          `${normalizedBaseUrl}/api/requirements-copilot/sessions/${requirementInterviewId}/answer`,
          {
            question_id: questionId,
            answer,
            actor
          }
        )
      );
    },
    async generateRequirementsCopilotDraft(
      requirementInterviewId: string
    ): Promise<RequirementsDraftResult> {
      return mapRequirementsDraftResult(
        await postJSON<RawRequirementsDraftResult>(
          `${normalizedBaseUrl}/api/requirements-copilot/sessions/${requirementInterviewId}/generate-draft`,
          {}
        )
      );
    },
    /** FR-15: turn a document's extracted requirements into a DRAFT version. */
    async promoteExtractedRequirements(
      documentId: string,
      actor = "workspace-ui"
    ): Promise<RequirementVersion> {
      return mapRequirementVersion(
        await postJSON<RawRequirementVersion>(
          `${normalizedBaseUrl}/api/documents/${documentId}/promote-requirements`,
          { actor }
        )
      );
    },
    /** FR-15: activate a DRAFT version, superseding the prior approved set. */
    async approveRequirementVersion(
      requirementVersionId: string,
      approvedBy = "workspace-ui"
    ): Promise<RequirementVersion> {
      return mapRequirementVersion(
        await postJSON<RawRequirementVersion>(
          `${normalizedBaseUrl}/api/requirement-versions/${requirementVersionId}/approve`,
          { approved_by: approvedBy }
        )
      );
    },
    async approveRequirementsCopilotDraft(
      requirementInterviewId: string,
      version: number | null,
      approvedBy = "workspace-ui"
    ): Promise<RequirementVersion> {
      // Pass `version` only when explicitly provided so the backend can
      // auto-increment to max(existing.version)+1.
      const body: Record<string, unknown> = { approved_by: approvedBy };
      if (version !== null && version !== undefined) {
        body.version = version;
      }
      return mapRequirementVersion(
        await postJSON<RawRequirementVersion>(
          `${normalizedBaseUrl}/api/requirements-copilot/sessions/${requirementInterviewId}/approve`,
          body
        )
      );
    },
    async getWorkflowDAG(runId: string): Promise<WorkflowDAGView> {
      return mapWorkflowDAGView(
        await getJSON<RawWorkflowDAGView>(
          `${normalizedBaseUrl}/api/runs/${runId}/workflow-dag`
        )
      );
    },
    async getReportBundle(reportId: string): Promise<ReportBundle> {
      return mapReportBundle(
        await getJSON<RawReportBundle>(
          `${normalizedBaseUrl}/api/reports/${reportId}`
        )
      );
    },
    async publishReport(reportId: string, actor: string): Promise<ReportBundle> {
      return mapReportBundle(
        await postJSON<RawReportBundle>(
          `${normalizedBaseUrl}/api/reports/${reportId}/publish`,
          { actor }
        )
      );
    },
    async exportReport(
      reportId: string,
      format: ReportExportFormat
    ): Promise<ReportExportDownload> {
      // Backend returns the rendered document as raw text (HTML or Markdown)
      // with a Content-Disposition attachment header. We bypass the JSON
      // helpers and read the response as a Blob so the browser can save it
      // directly via createObjectURL.
      const response = await fetch(
        `${normalizedBaseUrl}/api/reports/${reportId}/export?format=${encodeURIComponent(format)}`,
        { method: "GET" }
      );
      if (!response.ok) {
        const message = await response.text().catch(() => "");
        throw new Error(
          `Report export failed (${response.status}): ${message || response.statusText}`
        );
      }
      // Prefer the server-supplied filename so audit links + downloads agree
      // on naming. Fall back to a deterministic name when the header is
      // missing (e.g. cors-stripped responses).
      const disposition = response.headers.get("Content-Disposition") ?? "";
      const filenameMatch = disposition.match(/filename="?([^";]+)"?/i);
      const filename =
        filenameMatch?.[1] ?? `report-${reportId}.${format === "markdown" ? "md" : "html"}`;
      const blob = await response.blob();
      return { blob, filename, format };
    },
    async exportWorkspaceBundle(workspaceId: string): Promise<WorkspaceBundle> {
      return mapWorkspaceBundle(
        await getJSON<RawWorkspaceBundle>(
          `${normalizedBaseUrl}/api/workspaces/${workspaceId}/export`
        )
      );
    },
    async importWorkspaceBundle(bundle: WorkspaceBundle): Promise<WorkspaceImportResult> {
      return mapWorkspaceImportResult(
        await postJSON<RawWorkspaceImportResult>(
          `${normalizedBaseUrl}/api/workspaces/import`,
          workspaceBundlePayload(bundle)
        )
      );
    },
    async getWorkflowRecoveryActions(runId: string): Promise<WorkflowRecoveryActions> {
      return getJSON<WorkflowRecoveryActions>(
        `${normalizedBaseUrl}/api/runs/${runId}/recovery-actions`
      );
    },
    async createWorkflowRun(
      workspaceId: string,
      input: CreateWorkflowRunInput
    ): Promise<CreateWorkflowRunResult> {
      const workflowRun = await postJSON<RawWorkflowRunSummary>(
        `${normalizedBaseUrl}/api/runs`,
        createWorkflowRunPayload(workspaceId, input)
      );
      return {
        workflowRun: mapWorkflowRunSummary(workflowRun),
        dagView: mapWorkflowRunToDAGView(workflowRun)
      };
    },
    async quickAnalysis(
      workspaceId: string,
      input: QuickAnalysisInput
    ): Promise<CreateWorkflowRunResult> {
      const workflowRun = await postJSON<RawWorkflowRunSummary>(
        `${normalizedBaseUrl}/api/workspaces/${workspaceId}/quick-analysis`,
        {
          graph_profile_id: input.graphProfileId,
          prompt: input.prompt,
          ...(input.workflowMode ? { workflow_mode: input.workflowMode } : {})
        }
      );
      return {
        workflowRun: mapWorkflowRunSummary(workflowRun),
        dagView: mapWorkflowRunToDAGView(workflowRun)
      };
    },
    async startWorkflowRun(runId: string): Promise<WorkflowRunSummary> {
      return mapWorkflowRunSummary(
        await postJSON<RawWorkflowRunSummary>(
          `${normalizedBaseUrl}/api/runs/${runId}/start`,
          {}
        )
      );
    },
    async cancelWorkflowRun(
      runId: string,
      actor?: string
    ): Promise<WorkflowRunSummary> {
      // FR-31a: cooperative cancel. The backend returns the persisted
      // WorkflowRun row after attempting delivery; the actual transition
      // to "cancelled" may be observed on a subsequent /status poll
      // because the supervisor only checks the cancel token between
      // steps.
      return mapWorkflowRunSummary(
        await postJSON<RawWorkflowRunSummary>(
          `${normalizedBaseUrl}/api/runs/${runId}/cancel`,
          actor ? { actor } : {}
        )
      );
    },
    async getWorkflowRunStatus(
      runId: string
    ): Promise<WorkflowRunStatusView> {
      // FR-31a: lightweight poll for the UI. The shape mirrors the
      // backend's get_workflow_run_status() exactly, snake_case →
      // camelCase via mapWorkflowRunStatusView.
      const raw = await getJSON<{
        run_id: string;
        workspace_id: string;
        workflow_mode: string;
        status: string;
        started_at: string | null;
        completed_at: string | null;
        executor_kind: string | null;
        last_outcome: string | null;
        errors: string[];
        supervisor: {
          supervised: boolean;
          outcome?: string;
          cancel_requested?: boolean;
        };
      }>(`${normalizedBaseUrl}/api/runs/${runId}/status`);
      return {
        runId: raw.run_id,
        workspaceId: raw.workspace_id,
        workflowMode: raw.workflow_mode,
        status: raw.status,
        startedAt: raw.started_at,
        completedAt: raw.completed_at,
        executorKind: raw.executor_kind,
        lastOutcome: raw.last_outcome,
        errors: raw.errors ?? [],
        supervisor: {
          supervised: raw.supervisor.supervised,
          outcome: raw.supervisor.outcome,
          cancelRequested: raw.supervisor.cancel_requested
        }
      };
    },
    async updateWorkflowStep(
      runId: string,
      stepId: string,
      status: WorkflowStepStatus
    ): Promise<WorkflowStepUpdateResult> {
      return mapWorkflowStepUpdateResult(
        await patchJSON<RawWorkflowStepUpdateResult>(
          `${normalizedBaseUrl}/api/runs/${runId}/steps/${stepId}`,
          { status }
        )
      );
    }
  };
}

export async function getJSON<T>(url: string): Promise<T> {
  return requestJSON<T>(url, { method: "GET" });
}

export async function postJSON<T>(url: string, body: Record<string, unknown>): Promise<T> {
  return requestJSON<T>(url, {
    method: "POST",
    body: JSON.stringify(body)
  });
}

export async function deleteJSON<T>(url: string): Promise<T> {
  return requestJSON<T>(url, { method: "DELETE" });
}

export async function putJSON<T>(url: string, body: Record<string, unknown>): Promise<T> {
  return requestJSON<T>(url, {
    method: "PUT",
    body: JSON.stringify(body)
  });
}

export async function patchJSON<T>(url: string, body: Record<string, unknown>): Promise<T> {
  return requestJSON<T>(url, {
    method: "PATCH",
    body: JSON.stringify(body)
  });
}

async function requestJSON<T>(url: string, init: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      ...init.headers
    }
  });

  if (!response.ok) {
    // Surface the server's own explanation. The API answers domain failures
    // with {"error": "...", "detail": "..."} where the detail is the useful
    // part — which graph profile blocks a delete, which existing profile a
    // duplicate collides with, which arguments a request omitted. Throwing
    // only the status turned every one of those into "409 Conflict", so the
    // refusal messages written to name their blockers never reached anyone.
    let detail = "";
    try {
      const body = await response.json();
      if (body && typeof body === "object") {
        const raw = (body as { detail?: unknown; error?: unknown }).detail;
        if (typeof raw === "string" && raw.trim()) {
          detail = raw.trim();
        }
      }
    } catch {
      // Empty or non-JSON body (a proxy error page, a dropped connection).
      // Fall through to the status line, which is all we know.
    }
    throw new Error(
      detail || `Product API request failed: ${response.status} ${response.statusText}`
    );
  }

  return response.json() as Promise<T>;
}

export function mapWorkspaceOverview(raw: RawWorkspaceOverview): WorkspaceOverview {
  return {
    workspace: raw.workspace,
    counts: raw.counts,
    latestConnectionProfiles: (raw.latest_connection_profiles ?? []).map(
      mapConnectionProfileSummary
    ),
    latestGraphProfiles: (raw.latest_graph_profiles ?? []).map(mapGraphProfileSummary),
    latestSourceDocuments: (raw.latest_source_documents ?? []).map(mapSourceDocumentSummary),
    latestRequirementVersions: (raw.latest_requirement_versions ?? []).map(
      mapRequirementVersion
    ),
    latestWorkflowRuns: raw.latest_workflow_runs,
    latestReports: raw.latest_reports,
    latestAuditEvents: raw.latest_audit_events ?? []
  };
}

export function mapWorkspaceSummary(raw: RawWorkspaceSummary): WorkspaceSummary {
  return {
    workspaceId: raw.workspace_id,
    customerName: raw.customer_name,
    projectName: raw.project_name,
    environment: raw.environment,
    description: raw.description ?? "",
    status: raw.status ?? "active",
    tags: raw.tags ?? [],
    activeGraphProfileId: (raw.active_graph_profile_id as string | null | undefined) ?? null
  };
}

export function mapGraphDiscoveryResult(raw: RawGraphDiscoveryResult): GraphDiscoveryResult {
  return {
    graphProfile: mapGraphProfileSummary(raw.graph_profile),
    schemaSummary: raw.schema_summary
  };
}

export function mapConnectionGraphSummary(
  raw: RawConnectionGraphSummary
): ConnectionGraphSummary {
  return {
    name: raw.name,
    isSystem: raw.is_system ?? raw.name.startsWith("_"),
    vertexCollections: raw.vertex_collections ?? [],
    edgeCollections: raw.edge_collections ?? [],
    orphanCollections: raw.orphan_collections ?? [],
    edgeDefinitions: raw.edge_definitions ?? [],
    vertexCount: raw.vertex_count ?? null,
    edgeCount: raw.edge_count ?? null
  };
}

export function mapConnectionGraphsResult(
  raw: RawConnectionGraphsResult
): ConnectionGraphsResult {
  return {
    connectionProfileId: raw.connection_profile_id,
    workspaceId: raw.workspace_id,
    database: raw.database,
    graphs: (raw.graphs ?? []).map(mapConnectionGraphSummary)
  };
}

export function mapRequirementInterview(raw: RawRequirementInterview): RequirementInterview {
  return {
    requirementInterviewId: raw.requirement_interview_id,
    workspaceId: raw.workspace_id,
    graphProfileId: raw.graph_profile_id,
    status: raw.status,
    domain: raw.domain,
    questions: raw.questions ?? [],
    answers: raw.answers ?? [],
    schemaObservations: raw.schema_observations ?? {},
    inferences: raw.inferences ?? [],
    assumptions: raw.assumptions ?? [],
    draftBrd: raw.draft_brd,
    provenanceLabels: raw.provenance_labels ?? [],
    metadata: raw.metadata ?? {}
  };
}

export function mapRequirementsDraftResult(
  raw: RawRequirementsDraftResult
): RequirementsDraftResult {
  return {
    requirementInterview: mapRequirementInterview(raw.requirement_interview),
    draftBrd: raw.draft_brd,
    provenanceLabels: raw.provenance_labels ?? []
  };
}

export function mapRequirementVersion(raw: RawRequirementVersion): RequirementVersion {
  return {
    requirementVersionId: raw.requirement_version_id,
    workspaceId: raw.workspace_id,
    version: raw.version,
    status: raw.status,
    requirementInterviewId: raw.requirement_interview_id,
    documentIds: raw.document_ids ?? [],
    summary: raw.summary ?? "",
    objectives: raw.objectives ?? [],
    requirements: raw.requirements ?? [],
    constraints: raw.constraints ?? [],
    approvedAt: raw.approved_at,
    metadata: raw.metadata ?? {}
  };
}

export function mapAnalysisExecution(raw: RawAnalysisExecution): AnalysisExecution {
  return {
    analysisExecutionId: raw.analysis_execution_id,
    workspaceId: raw.workspace_id,
    runId: raw.run_id,
    algorithm: raw.algorithm,
    status: raw.status,
    graphProfileId: raw.graph_profile_id,
    requirementVersionId: raw.requirement_version_id,
    useCaseId: raw.use_case_id,
    templateId: raw.template_id,
    templateName: raw.template_name ?? "",
    epochId: raw.epoch_id,
    algorithmVersion: raw.algorithm_version ?? "",
    parameters: raw.parameters ?? {},
    resultsLocation: raw.results_location,
    resultCount: raw.result_count ?? 0,
    performanceMetrics: raw.performance_metrics ?? {},
    errorMessage: raw.error_message,
    workflowMode: raw.workflow_mode,
    startedAt: raw.started_at,
    completedAt: raw.completed_at,
    metadata: raw.metadata ?? {}
  };
}

export function mapAnalysisEpoch(raw: RawAnalysisEpoch): AnalysisEpoch {
  return {
    analysisEpochId: raw.analysis_epoch_id,
    workspaceId: raw.workspace_id,
    name: raw.name,
    description: raw.description ?? "",
    timestamp: raw.timestamp,
    status: raw.status,
    tags: raw.tags ?? [],
    analysisCount: raw.analysis_count ?? 0,
    analysisExecutionIds: raw.analysis_execution_ids ?? []
  };
}

export function mapShardingProfile(raw: RawShardingProfile): ShardingProfile {
  return {
    deploymentKind: raw.deployment_kind ?? "unknown",
    isOneShard: raw.is_one_shard ?? false,
    isMultitenant: raw.is_multitenant ?? false,
    tenantKey: raw.tenant_key,
    shardKeys: raw.shard_keys ?? [],
    smartGraphAttributes: raw.smart_graph_attributes ?? [],
    maxNumberOfShards: raw.max_number_of_shards ?? 0,
    minReplicationFactor: raw.min_replication_factor,
    satelliteCollections: raw.satellite_collections ?? [],
    warnings: raw.warnings ?? []
  };
}

export function mapRetentionPolicy(raw: RawRetentionPolicy): RetentionPolicy {
  return {
    workspaceId: raw.workspace_id,
    configured: raw.configured ?? false,
    enabled: raw.enabled ?? false,
    draftRetentionDays: raw.draft_retention_days ?? 0,
    runRetentionDays: raw.run_retention_days ?? 0,
    documentRetentionDays: raw.document_retention_days ?? 0,
    reportSnapshotRetentionDays: raw.report_snapshot_retention_days ?? 0,
    auditLogRetentionDays: raw.audit_log_retention_days ?? 0,
    lastAppliedAt: raw.last_applied_at
  };
}

export function mapRetentionSweepResult(
  raw: RawRetentionSweepResult
): RetentionSweepResult {
  return {
    workspaceId: raw.workspace_id,
    // Default to true: treating an unknown response as "deleted" would be the
    // safe reading for a UI that warns about destructive actions.
    deleted: raw.deleted ?? true,
    enabled: raw.enabled ?? false,
    reason: raw.reason,
    counts: raw.counts ?? {},
    candidates: raw.candidates ?? {},
    protected: raw.protected ?? {},
    removed: raw.removed
  };
}

export function mapVerticalProjectImportResult(
  raw: RawVerticalProjectImportResult
): VerticalProjectImportResult {
  return {
    workspaceId: raw.workspace_id,
    vertical: raw.vertical ?? "unspecified",
    projectName: raw.project_name ?? "",
    counts: raw.counts ?? { use_cases: 0, templates: 0 },
    warnings: raw.warnings ?? []
  };
}

export function mapUseCase(raw: RawUseCase): UseCase {
  return {
    useCaseId: raw.use_case_id,
    workspaceId: raw.workspace_id,
    title: raw.title,
    description: raw.description ?? "",
    useCaseType: raw.use_case_type ?? "pattern",
    priority: raw.priority ?? "medium",
    status: raw.status,
    origin: raw.origin ?? "manual",
    requirementVersionId: raw.requirement_version_id,
    relatedRequirements: raw.related_requirements ?? [],
    graphAlgorithms: raw.graph_algorithms ?? [],
    dataNeeds: raw.data_needs ?? [],
    expectedOutputs: raw.expected_outputs ?? [],
    successMetrics: raw.success_metrics ?? [],
    reviewedBy: raw.reviewed_by,
    reviewedAt: raw.reviewed_at,
    reviewNote: raw.review_note ?? "",
    createdAt: raw.created_at,
    createdBy: raw.created_by
  };
}

export function mapAnalysisTemplate(raw: RawAnalysisTemplate): AnalysisTemplate {
  return {
    analysisTemplateId: raw.analysis_template_id,
    workspaceId: raw.workspace_id,
    name: raw.name,
    lineageId: raw.lineage_id ?? raw.analysis_template_id,
    description: raw.description ?? "",
    algorithm: raw.algorithm ?? "",
    parameters: raw.parameters ?? {},
    config: raw.config ?? {},
    version: raw.version ?? 1,
    status: raw.status,
    useCaseId: raw.use_case_id,
    estimatedRuntimeSeconds: raw.estimated_runtime_seconds,
    supersededBy: raw.superseded_by,
    approvedBy: raw.approved_by,
    approvedAt: raw.approved_at,
    createdAt: raw.created_at,
    metadata: raw.metadata ?? {}
  };
}

export function mapAnalysisCatalogView(
  raw: RawAnalysisCatalogView
): AnalysisCatalogView {
  return {
    workspaceId: raw.workspace_id,
    epochs: (raw.epochs ?? []).map(mapAnalysisEpoch),
    executions: (raw.executions ?? []).map(mapAnalysisExecution),
    templates: (raw.templates ?? []).map(mapAnalysisTemplate),
    useCases: (raw.use_cases ?? []).map(mapUseCase),
    requirements: raw.requirements ?? [],
    unresolvedTemplateIds: raw.unresolved_template_ids ?? [],
    unresolvedUseCaseIds: raw.unresolved_use_case_ids ?? []
  };
}

export function mapAnalysisExecutionComparison(
  raw: RawAnalysisExecutionComparison
): AnalysisExecutionComparison {
  return {
    workspaceId: raw.workspace_id,
    baselineExecutionId: raw.baseline_execution_id,
    executions: (raw.executions ?? []).map(mapAnalysisExecution),
    deltas: (raw.deltas ?? []).map((delta) => ({
      analysisExecutionId: delta.analysis_execution_id,
      resultCount: delta.result_count ?? 0,
      performanceMetrics: delta.performance_metrics ?? {}
    }))
  };
}

export function mapAnalysisLineage(raw: RawAnalysisLineage): AnalysisLineage {
  return {
    workspaceId: raw.workspace_id,
    execution: raw.execution ? mapAnalysisExecution(raw.execution) : null,
    reports: raw.reports ?? [],
    templateId: raw.template_id,
    useCaseId: raw.use_case_id,
    requirementVersionId: raw.requirement_version_id
  };
}

export function mapConnectionVerificationResult(
  raw: RawConnectionVerificationResult
): ConnectionVerificationResult {
  return {
    connectionProfileId: raw.connection_profile_id,
    workspaceId: raw.workspace_id,
    status: raw.status,
    verifiedAt: raw.verified_at,
    endpoint: raw.endpoint,
    database: raw.database,
    errorMessage: raw.error_message,
    gaeStatus: raw.gae_status
  };
}

export function mapConnectionProfileSummary(
  raw: RawConnectionProfileSummary
): ConnectionProfileSummary {
  return {
    connectionProfileId: raw.connection_profile_id,
    workspaceId: raw.workspace_id,
    name: raw.name,
    deploymentMode: raw.deployment_mode,
    endpoint: raw.endpoint,
    database: raw.database,
    username: raw.username,
    verifySsl: raw.verify_ssl ?? true,
    secretRefs: raw.secret_refs ?? {},
    lastVerificationStatus: raw.last_verification_status ?? "unknown",
    lastVerifiedAt: raw.last_verified_at,
    metadata: raw.metadata ?? {}
  };
}

export function mapGraphProfileSummary(raw: RawGraphProfileSummary): GraphProfileSummary {
  return {
    graphProfileId: raw.graph_profile_id,
    workspaceId: raw.workspace_id,
    connectionProfileId: raw.connection_profile_id,
    graphName: raw.graph_name,
    status: raw.status,
    version: raw.version ?? 1,
    vertexCollections: raw.vertex_collections ?? [],
    edgeCollections: raw.edge_collections ?? [],
    edgeDefinitions: raw.edge_definitions ?? [],
    collectionRoles: raw.collection_roles ?? {},
    counts: raw.counts ?? {},
    shardingProfile: raw.analyzer_metadata?.sharding_profile
      ? mapShardingProfile(raw.analyzer_metadata.sharding_profile)
      : null
  };
}

export function mapSourceDocumentSummary(raw: RawSourceDocumentSummary): SourceDocumentSummary {
  return {
    documentId: raw.document_id,
    workspaceId: raw.workspace_id,
    filename: raw.filename,
    mimeType: raw.mime_type,
    sha256: raw.sha256 ?? "",
    storageMode: raw.storage_mode ?? "unknown",
    storageUri: raw.storage_uri,
    extractedText: raw.extracted_text,
    uploadedAt: raw.uploaded_at,
    metadata: raw.metadata ?? {}
  };
}

export function mapWorkflowDAGView(raw: RawWorkflowDAGView): WorkflowDAGView {
  return {
    runId: raw.run_id,
    workspaceId: raw.workspace_id,
    status: raw.status,
    workflowMode: raw.workflow_mode,
    nodes: raw.nodes.map(mapWorkflowNode),
    edges: raw.edges.map(mapWorkflowEdge),
    warnings: raw.warnings,
    errors: raw.errors
  };
}

export function mapWorkflowRunSummary(raw: RawWorkflowRunSummary): WorkflowRunSummary {
  return {
    runId: raw.run_id,
    workspaceId: raw.workspace_id,
    workflowMode: raw.workflow_mode,
    status: raw.status,
    startedAt: raw.started_at,
    completedAt: raw.completed_at
  };
}

export function mapWorkflowRunToDAGView(raw: RawWorkflowRunSummary): WorkflowDAGView {
  return {
    runId: raw.run_id,
    workspaceId: raw.workspace_id,
    status: raw.status,
    workflowMode: raw.workflow_mode,
    nodes: (raw.steps ?? []).map((step) => ({
      id: step.step_id,
      label: step.label,
      status: step.status,
      agentName: step.agent_name,
      artifactCount: step.artifact_refs?.length ?? 0,
      warningCount: step.warnings?.length ?? 0,
      errorCount: step.errors?.length ?? 0,
      startedAt: step.started_at,
      completedAt: step.completed_at,
      durationMs: step.duration_ms,
      retryCount: step.retry_count,
      checkpointId: step.checkpoint_id,
      inputs: step.inputs,
      outputs: step.outputs,
      artifactRefs: step.artifact_refs,
      warningMessages: step.warnings,
      errorMessages: step.errors,
      cost: step.cost
    })),
    edges: (raw.dag_edges ?? []).map((edge) => ({
      id: `${edge.from_step_id}-${edge.to_step_id}`,
      from: edge.from_step_id,
      to: edge.to_step_id,
      label: edge.label
    })),
    warnings: raw.warnings ?? [],
    errors: raw.errors ?? []
  };
}

export function mapWorkflowStepUpdateResult(
  raw: RawWorkflowStepUpdateResult
): WorkflowStepUpdateResult {
  return {
    workflowRun: mapWorkflowRunSummary(raw.workflow_run),
    dagView: mapWorkflowDAGView(raw.dag_view)
  };
}

export function mapWorkspaceHealth(raw: RawWorkspaceHealth): WorkspaceHealth {
  return {
    workspaceId: raw.workspace_id,
    status: raw.status,
    counts: raw.counts,
    issues: raw.issues.map((issue) => ({
      severity: issue.severity,
      code: issue.code,
      message: issue.message,
      entityIds: issue.entity_ids ?? []
    }))
  };
}

export function mapReportBundle(raw: RawReportBundle): ReportBundle {
  return {
    manifest: {
      reportId: raw.manifest.report_id,
      workspaceId: raw.manifest.workspace_id,
      runId: raw.manifest.run_id,
      title: raw.manifest.title,
      status: raw.manifest.status,
      summary: raw.manifest.summary ?? "",
      version: raw.manifest.version ?? 1
    },
    sections: raw.sections
      .map(mapReportSection)
      .sort((left, right) => left.order - right.order),
    charts: raw.charts.map(mapChartSpec),
    snapshots: raw.snapshots
  };
}

export function mapWorkspaceBundle(raw: RawWorkspaceBundle): WorkspaceBundle {
  return {
    schemaVersion: raw.schema_version,
    workspace: raw.workspace,
    connectionProfiles: raw.connection_profiles ?? [],
    graphProfiles: raw.graph_profiles ?? [],
    sourceDocuments: raw.source_documents ?? [],
    requirementInterviews: raw.requirement_interviews ?? [],
    requirementVersions: raw.requirement_versions ?? [],
    workflowRuns: raw.workflow_runs ?? [],
    reports: raw.reports ?? [],
    auditEvents: raw.audit_events ?? []
  };
}

export function mapWorkspaceImportResult(
  raw: RawWorkspaceImportResult
): WorkspaceImportResult {
  return {
    workspaceId: raw.workspace_id,
    counts: raw.counts
  };
}

export function workspaceAssetsFromOverview(overview: WorkspaceOverview): WorkspaceAsset[] {
  // A workspace can hold profiles across several databases, and every row that
  // does not name one forces the reader to open it to find out. Resolve the
  // lookups once, up front, so connection profiles, graph profiles, runs and
  // reports can all say where they point.
  const graphProfilesById = new Map(
    overview.latestGraphProfiles.map((profile) => [profile.graphProfileId, profile])
  );
  const connectionProfilesById = new Map(
    overview.latestConnectionProfiles.map((profile) => [
      profile.connectionProfileId,
      profile
    ])
  );

  function databaseFor(connectionProfileId: string | null | undefined): string {
    if (!connectionProfileId) {
      return "";
    }
    return connectionProfilesById.get(connectionProfileId)?.database ?? "";
  }

  function describeTarget(graphProfileId: string | null | undefined): string {
    if (!graphProfileId) {
      return "";
    }
    const graphProfile = graphProfilesById.get(graphProfileId);
    if (!graphProfile) {
      return "";
    }
    const database = databaseFor(graphProfile.connectionProfileId);
    const graphName = graphProfile.graphName || "all collections";
    return database ? `${graphName} in ${database}` : graphName;
  }

  const connectionProfileAssets = overview.latestConnectionProfiles.map((profile) => ({
    id: profile.connectionProfileId,
    kind: "connection-profile" as const,
    label: profile.name,
    // The profile name is free text and frequently is not the database name —
    // "addtech-prod-demo" points at "addtech-knowledge-graph" — so the row has
    // to say which database it actually reaches.
    description: profile.database
      ? `${profile.deploymentMode} connection (${profile.lastVerificationStatus}) · ${profile.database}`
      : `${profile.deploymentMode} connection (${profile.lastVerificationStatus})`
  }));
  const graphProfileAssets = overview.latestGraphProfiles.map((profile) => {
    const database = databaseFor(profile.connectionProfileId);
    return {
      id: profile.graphProfileId,
      kind: "graph-profile" as const,
      // A database-scoped profile has no named graph; call it what the
      // active-graph selector calls it rather than rendering an empty label.
      label: profile.graphName || "all collections",
      description: database
        ? `Graph profile (${profile.status}) · ${database}`
        : `Graph profile (${profile.status})`
    };
  });
  const documentAssets = overview.latestSourceDocuments.map((document) => ({
    id: document.documentId,
    kind: "document" as const,
    label: document.filename,
    description: document.mimeType
  }));
  // Project ONE consolidated "Requirements" row regardless of how many
  // RequirementVersion records exist (v1, v2,…). The id is synthetic so it
  // stays stable as new versions are approved and prior versions flip to
  // SUPERSEDED. Description shows the active version + history depth so the
  // user gets a one-glance summary without expanding the canvas. When no
  // versions exist yet the row is omitted entirely (caller decides whether
  // to surface a "Start Requirements Copilot" affordance elsewhere).
  const sortedVersions = [...overview.latestRequirementVersions].sort(
    (a, b) => b.version - a.version
  );
  const activeRequirementVersion =
    sortedVersions.find((version) => version.status === "approved") ??
    sortedVersions[0] ??
    null;
  // Requirements are deliberately NOT labelled with a graph or database.
  // A RequirementVersion has no graph link at all — only a Copilot interview
  // carries a graph_profile_id — and that is correct modelling: requirements
  // describe what the business wants, independent of which graph is later
  // analysed, and the same set can drive runs against several. Attributing one
  // to a database would assert something the record does not say. What a
  // version *can* honestly be traced to is the document it was extracted from.
  const documentNamesById = new Map(
    overview.latestSourceDocuments.map((document) => [
      document.documentId,
      document.filename
    ])
  );

  function describeRequirementSource(version: RequirementVersion): string {
    const names = (version.documentIds ?? [])
      .map((documentId) => documentNamesById.get(documentId))
      .filter((name): name is string => Boolean(name));
    if (names.length === 1) {
      return `from ${names[0]}`;
    }
    if (names.length > 1) {
      return `from ${names.length} documents`;
    }
    return "";
  }

  const requirementsAssets: WorkspaceAsset[] = activeRequirementVersion
    ? [
        {
          id: `requirements:${overview.workspace.workspace_id}`,
          kind: "requirements" as const,
          label: "Requirements",
          description: [
            `v${activeRequirementVersion.version} (${activeRequirementVersion.status})`,
            sortedVersions.length > 1
              ? `${sortedVersions.length - 1} prior version${
                  sortedVersions.length - 1 === 1 ? "" : "s"
                }`
              : "",
            describeRequirementSource(activeRequirementVersion)
          ]
            .filter(Boolean)
            .join(" · ")
        }
      ]
    : [];
  // A run records only a graph_profile_id; reports carry no graph profile at
  // all and inherit their run's. Resolve both through the maps above.
  const runTargetByRunId = new Map(
    overview.latestWorkflowRuns.map((run) => [
      run.run_id,
      describeTarget(run.graph_profile_id)
    ])
  );

  const runAssets = overview.latestWorkflowRuns.map((run) => {
    const target = runTargetByRunId.get(run.run_id);
    return {
      id: run.run_id,
      kind: "run" as const,
      label: `Run ${run.run_id}`,
      description: target
        ? `${run.workflow_mode} workflow (${run.status}) · ${target}`
        : `${run.workflow_mode} workflow (${run.status})`
    };
  });
  const reportAssets = overview.latestReports.map((report) => {
    // A report carries no graph profile of its own, so its target is its run's.
    const target = report.run_id ? runTargetByRunId.get(report.run_id) : undefined;
    return {
      id: report.report_id,
      kind: "report" as const,
      label: report.title,
      description: target
        ? `Report (${report.status}) · ${target}`
        : `Report (${report.status})`
    };
  });

  // FR-45..FR-48: always present, even with no analyses yet — this row is the
  // only entry point to the catalog, so hiding it when empty would make the
  // feature undiscoverable. The canvas renders its own empty state.
  const analysisCatalogAsset: WorkspaceAsset = {
    id: `analysis-catalog:${overview.workspace.workspace_id}`,
    kind: "analysis-catalog" as const,
    label: "Analysis Catalog",
    description: "Executions, epochs, comparison, and lineage"
  };

  const useCaseAsset: WorkspaceAsset = {
    id: `use-cases:${overview.workspace.workspace_id}`,
    kind: "use-cases" as const,
    label: "Use Cases & Templates",
    description: "Author, review, and version analysis templates"
  };

  const retentionAsset: WorkspaceAsset = {
    id: `retention:${overview.workspace.workspace_id}`,
    kind: "retention" as const,
    label: "Retention",
    description: "Configure retention windows and preview a sweep"
  };

  return [
    ...connectionProfileAssets,
    ...graphProfileAssets,
    ...requirementsAssets,
    ...documentAssets,
    ...runAssets,
    ...reportAssets,
    analysisCatalogAsset,
    useCaseAsset,
    retentionAsset
  ];
}

/** The server's login kind; anything unrecognised is the password path. */
export function mapConnectionLogin(raw: string | undefined): ConnectionLogin {
  return raw === "platform" ? "platform" : "password";
}

/** The secret references a new profile carries: none for the platform login
 * (each user acts as themselves), else the password's env-var name. */
export function connectionSecretRefs(
  input: CreateConnectionProfileInput
): Record<string, Record<string, string>> {
  if (input.login === "platform") {
    return { password: { kind: "platform_login" } };
  }
  const passwordSecretEnvVar = input.passwordSecretEnvVar?.trim() ?? "";
  return passwordSecretEnvVar
    ? { password: { kind: "env", ref: passwordSecretEnvVar } }
    : {};
}

function createConnectionProfilePayload(
  input: CreateConnectionProfileInput
): Record<string, unknown> {
  const secretRefs = connectionSecretRefs(input);

  return {
    name: input.name,
    deployment_mode: input.deploymentMode,
    endpoint: input.endpoint,
    database: input.database,
    username: input.username,
    verify_ssl: input.verifySsl,
    secret_refs: secretRefs
  };
}

function createWorkspacePayload(input: CreateWorkspaceInput): Record<string, unknown> {
  const description = input.description?.trim() ?? "";
  const actor = input.actor?.trim() ?? "";
  return {
    customer_name: input.customerName,
    project_name: input.projectName,
    environment: input.environment,
    ...(description ? { description } : {}),
    tags: input.tags ?? [],
    ...(actor ? { actor } : {})
  };
}

function discoverGraphProfilePayload(input: DiscoverGraphProfileInput): Record<string, unknown> {
  const graphName = input.graphName?.trim() ?? "";
  return {
    // When forceDatabaseScope is set the backend ignores graph_name and
    // creates the "default" (all-collections) profile, so we suppress
    // graph_name in that case to avoid a confusing payload.
    ...(graphName && !input.forceDatabaseScope ? { graph_name: graphName } : {}),
    sample_size: input.sampleSize,
    max_samples_per_collection: input.maxSamplesPerCollection,
    verify_system: input.verifySystem,
    ...(input.forceDatabaseScope ? { force_database_scope: true } : {})
  };
}

function startRequirementsCopilotPayload(
  input: StartRequirementsCopilotInput
): Record<string, unknown> {
  const domain = input.domain?.trim() ?? "";
  const createdBy = input.createdBy?.trim() ?? "";
  const basedOnVersionId = input.basedOnVersionId?.trim() ?? "";
  return {
    ...(domain ? { domain } : {}),
    ...(createdBy ? { created_by: createdBy } : {}),
    ...(basedOnVersionId ? { based_on_version_id: basedOnVersionId } : {})
  };
}

function workspaceBundlePayload(bundle: WorkspaceBundle): Record<string, unknown> {
  return {
    schema_version: bundle.schemaVersion,
    workspace: bundle.workspace,
    connection_profiles: bundle.connectionProfiles,
    graph_profiles: bundle.graphProfiles,
    source_documents: bundle.sourceDocuments,
    requirement_interviews: bundle.requirementInterviews,
    requirement_versions: bundle.requirementVersions,
    workflow_runs: bundle.workflowRuns,
    reports: bundle.reports,
    audit_events: bundle.auditEvents
  };
}

function createWorkflowRunPayload(
  workspaceId: string,
  input: CreateWorkflowRunInput
): Record<string, unknown> {
  const steps = input.stepLabels.map((label, index) => ({
    step_id: slugifyStepId(label, index),
    label,
    status: "pending"
  }));
  return {
    workspace_id: workspaceId,
    workflow_mode: input.workflowMode,
    steps,
    dag_edges: steps.slice(1).map((step, index) => ({
      from_step_id: steps[index].step_id,
      to_step_id: step.step_id
    }))
  };
}

function slugifyStepId(label: string, index: number): string {
  const slug = label
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
  return slug || `step-${index + 1}`;
}

function mapReportSection(raw: RawReportBundle["sections"][number]): ReportSection {
  return {
    sectionId: raw.section_id,
    order: raw.order,
    type: raw.type,
    title: raw.title,
    content: raw.content ?? {},
    evidenceRefs: raw.evidence_refs ?? []
  };
}

function mapChartSpec(raw: RawReportBundle["charts"][number]): ChartSpec {
  return {
    chartId: raw.chart_id,
    title: raw.title,
    chartType: raw.chart_type,
    dataSource: raw.data_source ?? {},
    data: raw.data ?? {},
    encoding: raw.encoding ?? {}
  };
}

function mapWorkflowNode(raw: RawWorkflowDAGView["nodes"][number]): WorkflowDAGNode {
  return {
    id: raw.id,
    label: raw.label,
    status: raw.status,
    agentName: raw.agent_name,
    artifactCount: raw.artifact_refs?.length ?? 0,
    warningCount: raw.warnings?.length ?? 0,
    errorCount: raw.errors?.length ?? 0,
    startedAt: raw.started_at,
    completedAt: raw.completed_at,
    durationMs: raw.duration_ms,
    retryCount: raw.retry_count,
    checkpointId: raw.checkpoint_id,
    inputs: raw.inputs,
    outputs: raw.outputs,
    artifactRefs: raw.artifact_refs,
    warningMessages: raw.warnings,
    errorMessages: raw.errors,
    cost: raw.cost
  };
}

function mapWorkflowEdge(raw: RawWorkflowDAGView["edges"][number]): WorkflowDAGEdge {
  return {
    id: raw.id,
    from: raw.from,
    to: raw.to,
    label: raw.label
  };
}

/**
 * FR-37: route a workflow step's artifact reference to the asset that shows it.
 *
 * Refs carry a backend `type` (`report`, `graph_profile`, …) while the Assets
 * panel keys off UI `kind`s, and the two do not use the same vocabulary. Some
 * assets are per-record and match by id; others are synthetic singletons
 * (Requirements, Use Cases & Templates, Analysis Catalog) whose ids are
 * workspace-scoped strings, so those match on kind alone and the ref's id is
 * used afterwards to focus the right row inside the canvas.
 *
 * Returns null when nothing can show the ref, so the caller can leave it as
 * plain text rather than rendering a control that does nothing.
 */
export function resolveArtifactAsset(
  ref: WorkflowArtifactRef,
  assets: WorkspaceAsset[]
): WorkspaceAsset | null {
  const byId: Record<string, WorkspaceAsset["kind"]> = {
    report: "report",
    graph_profile: "graph-profile",
    connection_profile: "connection-profile",
    source_document: "document",
    run: "run"
  };
  const bySingleton: Record<string, WorkspaceAsset["kind"]> = {
    requirement_version: "requirements",
    use_case: "use-cases",
    analysis_template: "use-cases",
    analysis_execution: "analysis-catalog"
  };

  const idKind = byId[ref.type];
  if (idKind) {
    return assets.find((asset) => asset.kind === idKind && asset.id === ref.id) ?? null;
  }

  const singletonKind = bySingleton[ref.type];
  if (singletonKind) {
    return assets.find((asset) => asset.kind === singletonKind) ?? null;
  }

  return null;
}
