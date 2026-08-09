/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: "standalone",
  // Playwright uses either localhost or 127.0.0.1 for its isolated dev server.
  // Allow both so development tests do not emit cross-origin asset warnings.
  allowedDevOrigins: ["localhost", "127.0.0.1"],
};

module.exports = nextConfig;
