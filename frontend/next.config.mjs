/** @type {import('next').NextConfig} */

// The BYOC bundle has no Node server — FastAPI serves the built files — so the
// frontend ships as a static export.
//
// Next emits root-absolute asset URLs (/_next/static/...). The platform mounts
// the service under /_service/uds/_global/<instance>/ and envoy strips that
// prefix before the pod sees it, so the *server* gets clean paths but the
// *browser* does not: without the prefix baked in, every asset resolves against
// the cluster root and the page renders blank with a green light on every
// health check.
//
// So the mount path is a build input. SERVICE_URL_PATH_PREFIX is set by
// scripts/platform/package.sh from --instance/--db, and the deploy pre-flight
// refuses to upload a bundle whose baked prefix does not match where the
// deploy will actually mount it. Empty for local development, where the app is
// served from the root.
const prefix = (process.env.SERVICE_URL_PATH_PREFIX ?? "").replace(/\/+$/, "");

const nextConfig = {
  output: "export",
  // basePath rewrites in-app routes; assetPrefix rewrites static asset URLs.
  // Both are needed: one without the other gives either broken navigation or
  // broken assets.
  ...(prefix ? { basePath: prefix, assetPrefix: prefix } : {}),
  // Export writes workspace/index.html rather than workspace.html, so the path
  // resolves when served by a plain static file handler.
  trailingSlash: true,
  // next/image's optimizer needs a running Node server; there is none here.
  images: { unoptimized: true },
};

export default nextConfig;
