"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

/** Sends `/` to the workspace.
 *
 * `redirect()` from next/navigation is a server redirect, which a static export
 * cannot perform — there is no server to issue the 307. Doing it in the browser
 * works both in dev and from the files FastAPI serves inside the BYOC bundle.
 *
 * `replace` rather than `push` so the redirect does not sit in history and trap
 * the back button on `/`.
 */
export default function HomePage() {
  const router = useRouter();

  useEffect(() => {
    router.replace("/workspace");
  }, [router]);

  return null;
}
