import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";
import type { ViteDevServer } from "vite";

const launcherInstanceId = process.env.BRAINLEARN_LAUNCH_INSTANCE_ID;

const launcherReadinessPlugin = {
  name: "brainlearn-launcher-readiness",
  configureServer(server: ViteDevServer) {
    if (!launcherInstanceId) return;
    server.middlewares.use((_request, response, next) => {
      response.setHeader("X-BrainLearn-Launcher-Instance", launcherInstanceId);
      next();
    });
  },
};

export default defineConfig({
  plugins: [react(), launcherReadinessPlugin],
  test: {
    environment: "jsdom",
  },
  server: {
    host: "127.0.0.1",
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
});
