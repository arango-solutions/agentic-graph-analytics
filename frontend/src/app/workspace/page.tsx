"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { WorkspaceShell } from "@/components/workspace/WorkspaceShell";

/** Reads the deep-link query params on the client.
 *
 * This page used to take `searchParams` as a server prop, which cannot work in
 * the static export the BYOC bundle needs: there is no server at request time,
 * so Next refuses to prerender the route ("used `searchParams.workspaceId`").
 * `useSearchParams` resolves the same values in the browser, where the answer
 * actually lives.
 *
 * Params handled here:
 *   workspaceId        — the workspace to open
 *   runId              — a run to select on load
 *   requirementVersion — deep-link target for the Requirements canvas; the
 *                        version dropdown opens on this id (read-only history
 *                        mode if it is not the active version).
 */
function WorkspaceFromQuery() {
  const searchParams = useSearchParams();
  return (
    <WorkspaceShell
      initialWorkspaceId={searchParams.get("workspaceId") ?? undefined}
      initialRunId={searchParams.get("runId") ?? undefined}
      initialRequirementVersionId={
        searchParams.get("requirementVersion") ?? undefined
      }
    />
  );
}

export default function WorkspacePage() {
  // useSearchParams suspends during prerender; without this boundary the
  // export fails again with a different message.
  return (
    <Suspense fallback={null}>
      <WorkspaceFromQuery />
    </Suspense>
  );
}
