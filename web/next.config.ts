import type { NextConfig } from "next";

// The installer is the one from the newest release: every release moves the v0
// tag, and https://projector.bot/install.sh serves install.sh at that tag. The
// rewrite proxies it rather than redirecting, so `curl -fsSL ... | bash`
// fetches it from this domain in one request.
const INSTALLER = "https://raw.githubusercontent.com/ninjudd/projector/v0/install.sh";

const nextConfig: NextConfig = {
  async rewrites() {
    return [{ source: "/install.sh", destination: INSTALLER }];
  },
};

export default nextConfig;
