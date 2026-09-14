import express from "express";
import { spawn } from "child_process";
import path from "path";
import fs from "fs";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const app = express();
const PORT = 3000;

app.use(express.json());
app.use(express.static(path.join(__dirname, "public")));

function runPythonBridge(args) {
  return new Promise((resolve, reject) => {
    const py = spawn("python3", [path.join(__dirname, "scripts", "api_bridge.py"), ...args]);
    let stdoutData = "";
    let stderrData = "";

    py.stdout.on("data", (data) => {
      stdoutData += data.toString();
    });

    py.stderr.on("data", (data) => {
      stderrData += data.toString();
    });

    py.on("close", (code) => {
      // Find JSON block in stdoutData
      const lines = stdoutData.trim().split("\n");
      let jsonStr = null;
      for (let i = lines.length - 1; i >= 0; i--) {
        const line = lines[i].trim();
        if (line.startsWith("{") && line.endsWith("}")) {
          jsonStr = line;
          break;
        }
      }

      if (jsonStr) {
        try {
          const parsed = JSON.parse(jsonStr);
          return resolve(parsed);
        } catch (e) {
          // fall through
        }
      }

      if (code !== 0) {
        return reject(new Error(stderrData || stdoutData || `Process exited with code ${code}`));
      }

      try {
        const parsed = JSON.parse(stdoutData.trim());
        resolve(parsed);
      } catch (err) {
        resolve({ ok: true, raw: stdoutData.trim() });
      }
    });

    py.on("error", (err) => {
      reject(err);
    });
  });
}

// API Routes
app.get("/api/status", async (req, res) => {
  try {
    const data = await runPythonBridge(["status"]);
    res.json(data);
  } catch (err) {
    res.status(500).json({ ok: false, error: err.message });
  }
});

app.post("/api/command", async (req, res) => {
  const query = req.body?.query;
  if (!query || typeof query !== "string") {
    return res.status(400).json({ ok: false, error: "query parameter required" });
  }
  try {
    const data = await runPythonBridge(["command", query]);
    res.json(data);
  } catch (err) {
    res.status(500).json({ ok: false, error: err.message });
  }
});

app.post("/api/tool", async (req, res) => {
  const { name, args } = req.body || {};
  if (!name) {
    return res.status(400).json({ ok: false, error: "Tool name is required" });
  }
  try {
    const argsJson = JSON.stringify(args || {});
    const data = await runPythonBridge(["tool", name, argsJson]);
    res.json(data);
  } catch (err) {
    res.status(500).json({ ok: false, error: err.message });
  }
});

app.get("/api/config", (req, res) => {
  const configPath = path.join(__dirname, "config.json");
  try {
    const content = fs.readFileSync(configPath, "utf-8");
    res.json({ ok: true, config: JSON.parse(content) });
  } catch (err) {
    res.status(500).json({ ok: false, error: err.message });
  }
});

app.post("/api/config", (req, res) => {
  const configPath = path.join(__dirname, "config.json");
  const newConfig = req.body;
  if (!newConfig || typeof newConfig !== "object") {
    return res.status(400).json({ ok: false, error: "Invalid config object" });
  }
  try {
    fs.writeFileSync(configPath, JSON.stringify(newConfig, null, 2), "utf-8");
    res.json({ ok: true, message: "Configuration saved successfully" });
  } catch (err) {
    res.status(500).json({ ok: false, error: err.message });
  }
});

// Fallback for SPA
app.get("*", (req, res) => {
  res.sendFile(path.join(__dirname, "public", "index.html"));
});

app.listen(PORT, "0.0.0.0", () => {
  console.log(`Jarvis Web Assistant running on http://0.0.0.0:${PORT}`);
});
