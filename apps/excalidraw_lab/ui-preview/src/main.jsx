import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { Excalidraw, exportToBlob } from "@excalidraw/excalidraw";
import "@excalidraw/excalidraw/index.css";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Clock,
  Download,
  FileJson,
  Home,
  RefreshCw,
  Ruler,
  Save,
  Send,
  Settings2,
  ShieldAlert,
  Square,
  UploadCloud,
  Wifi,
  X,
} from "lucide-react";
import { prepareRasterExportElements } from "./rasterInkElements.js";
import {
  sendBadgeLabel,
  summaryDisplayWarnings,
  thicknessDisplayRows,
} from "./jobSummaryView.js";
import {
  CUSTOM_OPTION_LABEL,
  CUSTOM_OPTION_VALUE,
  applyMaterialSelectChange,
  findMaterialOption,
  loadUiMaterialOptions,
  restoreMaterialSelectState,
  returnToLibrary,
} from "./materialSelectState.js";
import "./styles.css";

const CONFIG_KEYS = ["material", "thickness_mm", "width_mm", "height_mm", "mode", "network_host", "network_telnet_port"];
const EXCALIDRAW_LANG_CODE = "zh-CN";

const DEFAULT_FORM = {
  material: localStorage.getItem("laserLab.material") || "椴木",
  thickness_mm: localStorage.getItem("laserLab.thickness_mm") || "3",
  width_mm: localStorage.getItem("laserLab.width_mm") || "",
  height_mm: localStorage.getItem("laserLab.height_mm") || "",
  mode: localStorage.getItem("laserLab.mode") || "raster",
  network_host: localStorage.getItem("laserLab.network_host") || "",
  network_telnet_port: localStorage.getItem("laserLab.network_telnet_port") || "23",
  output_format: "gcode",
};

function App() {
  const [excalidrawAPI, setExcalidrawAPI] = useState(null);
  const [form, setForm] = useState(DEFAULT_FORM);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("准备就绪");
  const [workflowId, setWorkflowId] = useState("");
  const [result, setResult] = useState(null);
  const [lastScene, setLastScene] = useState(null);
  const [connectionBusy, setConnectionBusy] = useState(false);
  const [connectionResult, setConnectionResult] = useState(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [materialLibrary, setMaterialLibrary] = useState([]);
  const [materialMode, setMaterialMode] = useState("custom");
  const [materialSelectValue, setMaterialSelectValue] = useState(CUSTOM_OPTION_VALUE);

  const web = result?.result?.web || {};
  const lab = result?.result?.draw_lab || {};
  const jobSummary = lab.job_summary || null;
  const rawText = useMemo(() => (result ? JSON.stringify(result, null, 2) : ""), [result]);
  const canActOnWorkflow = Boolean(workflowId);
  const hostReady = Boolean(form.network_host.trim());
  const summaryAllowsSend = jobSummary?.can_send !== false;
  const canConfirmSend = canActOnWorkflow && hostReady && summaryAllowsSend;

  function updateForm(key, value) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const loaded = await loadUiMaterialOptions(fetch);
      if (cancelled) return;
      const restored = restoreMaterialSelectState({
        materials: loaded.materials,
        savedMaterial: form.material,
        savedThickness: form.thickness_mm,
      });
      setMaterialLibrary(restored.materials);
      setMaterialMode(restored.mode);
      setMaterialSelectValue(restored.selectValue);
      setForm((current) => ({
        ...current,
        material: restored.material || current.material,
        thickness_mm: restored.thickness_mm || current.thickness_mm,
      }));
    })();
    return () => {
      cancelled = true;
    };
    // Intentionally run once on mount to restore from localStorage defaults.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function handleMaterialSelectChange(selectValue) {
    const next = applyMaterialSelectChange({
      materials: materialLibrary,
      currentThickness: form.thickness_mm,
      selectValue,
      customMaterial: form.material,
    });
    setMaterialLibrary(next.materials);
    setMaterialMode(next.mode);
    setMaterialSelectValue(next.selectValue);
    setForm((current) => ({
      ...current,
      material: next.material,
      thickness_mm: next.thickness_mm,
    }));
  }

  function handleThicknessSelectChange(value) {
    updateForm("thickness_mm", value);
  }

  function handleReturnToLibrary() {
    const next = returnToLibrary({
      materials: materialLibrary,
      preferredMaterial: form.material,
      preferredThickness: form.thickness_mm,
    });
    setMaterialLibrary(next.materials);
    setMaterialMode(next.mode);
    setMaterialSelectValue(next.selectValue);
    setForm((current) => ({
      ...current,
      material: next.material,
      thickness_mm: next.thickness_mm,
    }));
  }

  function saveConfiguration() {
    CONFIG_KEYS.forEach((key) => {
      localStorage.setItem(`laserLab.${key}`, form[key] ?? "");
    });
    setStatus("配置已保存");
  }

  async function exportScene({ forLaser = false } = {}) {
    if (!excalidrawAPI) {
      throw new Error("画板还没有加载完成");
    }
    const elements = excalidrawAPI.getSceneElements();
    const activeElements = elements.filter((element) => !element.isDeleted);
    if (activeElements.length === 0) {
      throw new Error("画板是空的");
    }
    const appState = excalidrawAPI.getAppState();
    const files = excalidrawAPI.getFiles();
    const exportElements = forLaser ? prepareRasterExportElements(activeElements, form.mode) : activeElements;
    const exportAppState = {
      ...appState,
      exportBackground: true,
      exportWithDarkMode: false,
      viewBackgroundColor: "#ffffff",
    };
    const blob = await exportToBlob({
      elements: exportElements,
      appState: exportAppState,
      files,
      mimeType: "image/png",
      quality: 1,
      exportPadding: 16,
    });
    const scene = {
      type: "excalidraw",
      version: 2,
      source: "excalidraw-laser-lab",
      elements,
      appState: {
        gridSize: appState.gridSize,
        name: appState.name,
        viewBackgroundColor: appState.viewBackgroundColor,
      },
      files,
    };
    setLastScene(scene);
    return { blob, scene };
  }

  async function generatePreview() {
    setBusy(true);
    setStatus("导出画板中...");
    try {
      const { blob, scene } = await exportScene({ forLaser: true });
      const imageDataUrl = await blobToDataUrl(blob);
      setStatus("上传并生成预览中...");
      const payload = {
        image_data_url: imageDataUrl,
        scene_json: scene,
        material: form.material,
        thickness_mm: form.thickness_mm,
        width_mm: form.width_mm,
        height_mm: form.height_mm,
        mode: form.mode,
        output_format: form.output_format,
        task_type: taskTypeForMode(form.mode),
        network_host: form.network_host,
        network_telnet_port: form.network_telnet_port,
      };
      if (form.mode === "raster") {
        payload.dither_algorithm = "threshold";
        payload.raster_scan_direction = "horizontal";
        payload.raster_output_strategy = "scanline";
        payload.raster_quality_strategy = "manual";
      }
      const data = await postJson("/api/draw/preview", payload);
      setResult(data);
      const nextWorkflowId = data?.result?.workflow_id || data?.result?.workflow?.workflow_id || "";
      setWorkflowId(nextWorkflowId);
      setConfirmOpen(false);
      setStatus(data.success ? data.result?.speech || "预览已生成" : data.result || "预览失败");
    } catch (error) {
      setStatus(`失败：${error.message || error}`);
      setResult({ success: false, result: String(error.message || error) });
    } finally {
      setBusy(false);
    }
  }

  function openSendConfirmation() {
    if (!workflowId) return;
    if (!hostReady) {
      setStatus("请先填写设备 host/IP");
      return;
    }
    if (!summaryAllowsSend) {
      setStatus("当前预览摘要标记为不可发送，请先调整参数后重新生成");
      return;
    }
    setConfirmOpen(true);
  }

  async function confirmSend() {
    if (!workflowId || !hostReady || !summaryAllowsSend) {
      setStatus("请先生成预览并填写设备 host/IP");
      return;
    }
    setBusy(true);
    setStatus("提交确认发送...");
    try {
      const data = await postJson("/api/workflow", {
        action: "confirm_send",
        workflow_id: workflowId,
        confirmed: true,
        connection_mode: "network",
        network_host: form.network_host,
        network_telnet_port: form.network_telnet_port,
        run_in_background: true,
        wait_for_response: true,
      });
      setResult(data);
      setConfirmOpen(false);
      setStatus(data.success ? data.result?.speech || "已交给发送门禁处理" : data.result || "发送被拒绝");
    } catch (error) {
      setStatus(`请求失败：${error.message || error}`);
    } finally {
      setBusy(false);
    }
  }

  async function testConnection() {
    if (!hostReady) {
      setConnectionResult({ success: false, result: "设备 host/IP 不能为空" });
      setStatus("请先填写设备 host/IP");
      return;
    }
    setConnectionBusy(true);
    setStatus("测试设备连接...");
    try {
      const data = await postJson("/api/connection/check", {
        network_host: form.network_host,
        network_transport: "telnet",
        network_telnet_port: form.network_telnet_port,
        network_timeout: 3,
        include_detail: false,
      });
      setConnectionResult(data);
      setStatus(data.success ? data.result?.summary || "连接检查完成" : data.result || "连接检查失败");
    } catch (error) {
      const message = error.message || String(error);
      setConnectionResult({ success: false, result: message });
      setStatus(`连接检查失败：${message}`);
    } finally {
      setConnectionBusy(false);
    }
  }

  async function workflowAction(action) {
    if (!workflowId) return;
    setBusy(true);
    setStatus(action === "status" ? "查询状态..." : "提交取消...");
    try {
      const data = await postJson("/api/workflow", { action, workflow_id: workflowId });
      setResult(data);
      setStatus(data.success ? data.result?.speech || "已更新" : data.result || "操作失败");
    } catch (error) {
      setStatus(`请求失败：${error.message || error}`);
    } finally {
      setBusy(false);
    }
  }

  async function downloadPng() {
    const { blob } = await exportScene();
    downloadBlob(blob, "excalidraw-laser-lab.png");
  }

  async function downloadScene() {
    const { scene } = lastScene ? { scene: lastScene } : await exportScene();
    const blob = new Blob([JSON.stringify(scene, null, 2)], { type: "application/json" });
    downloadBlob(blob, "excalidraw-laser-lab.excalidraw.json");
  }

  function goHome() {
    window.location.href = "/";
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand-cluster">
          <div className="brand-mark" aria-hidden="true">
            <Activity size={21} />
          </div>
          <div>
            <div className="eyebrow">MOSS · LASER STUDIO</div>
            <div className="title-row">
              <h1>激光创作工作台</h1>
              <span className="preview-badge">界面测试版</span>
            </div>
          </div>
        </div>

        <div className="status-center" aria-live="polite">
          <span className={`status-dot ${busy ? "busy" : ""}`} />
          <div>
            <span>工作台状态</span>
            <strong>{status}</strong>
          </div>
        </div>

        <div className="topbar-actions">
          <button type="button" className="ghost-button" onClick={goHome}>
            <Home size={17} />
            首页
          </button>
          <button type="button" className="ghost-button" onClick={downloadScene} disabled={busy || !excalidrawAPI}>
            <FileJson size={17} />
            场景
          </button>
          <button type="button" className="ghost-button" onClick={downloadPng} disabled={busy || !excalidrawAPI}>
            <Download size={17} />
            导出 PNG
          </button>
        </div>
      </header>

      <section className="workspace">
        <aside className="control-panel">
          <div className="panel-heading">
            <div>
              <span className="section-index">01</span>
              <div>
                <span className="eyebrow">PROCESS SETTINGS</span>
                <h2>加工参数</h2>
              </div>
            </div>
            <Settings2 size={19} />
          </div>

          <section className="form-section">
            <div className="form-section-title">材料配置</div>
            <label>
              材料库
              <select
                value={materialSelectValue}
                onChange={(event) => handleMaterialSelectChange(event.target.value)}
              >
                {materialLibrary.map((item) => (
                  <option key={item.name} value={item.name}>
                    {item.name}
                  </option>
                ))}
                <option value={CUSTOM_OPTION_VALUE}>{CUSTOM_OPTION_LABEL}</option>
              </select>
            </label>
            {materialMode === "custom" ? (
              <label>
                材料名称
                <input value={form.material} onChange={(event) => updateForm("material", event.target.value)} />
              </label>
            ) : null}
            {materialMode === "library" && findMaterialOption(materialLibrary, form.material) ? (
              <label>
                厚度 mm
                <select
                  value={form.thickness_mm}
                  onChange={(event) => handleThicknessSelectChange(event.target.value)}
                >
                  {findMaterialOption(materialLibrary, form.material).thicknesses.map((value) => (
                    <option key={String(value)} value={String(value)}>
                      {value}
                    </option>
                  ))}
                </select>
              </label>
            ) : (
              <label>
                厚度 mm
                <input
                  type="number"
                  min="0"
                  step="0.1"
                  value={form.thickness_mm}
                  onChange={(event) => updateForm("thickness_mm", event.target.value)}
                />
              </label>
            )}
            {materialMode === "custom" && materialLibrary.length > 0 ? (
              <button type="button" className="text-button" onClick={handleReturnToLibrary}>
                返回材料库推荐
              </button>
            ) : null}
          </section>

          <section className="form-section">
            <div className="form-section-title">画布与策略</div>
            <div className="dimension-grid">
              <label>
                宽 mm
                <input
                  type="number"
                  min="0"
                  max="120"
                  step="0.1"
                  value={form.width_mm}
                  onChange={(event) => updateForm("width_mm", event.target.value)}
                  placeholder="自动"
                />
              </label>
              <label>
                高 mm
                <input
                  type="number"
                  min="0"
                  max="120"
                  step="0.1"
                  value={form.height_mm}
                  onChange={(event) => updateForm("height_mm", event.target.value)}
                  placeholder="自动"
                />
              </label>
            </div>
            <label>
              生成策略
              <select value={form.mode} onChange={(event) => updateForm("mode", event.target.value)}>
                <option value="raster">光栅雕刻 · raster</option>
                <option value="outline">轮廓雕刻 · outline</option>
                <option value="auto">自动判断 · auto</option>
              </select>
            </label>
          </section>

          <section className="form-section">
            <div className="form-section-title">设备连接</div>
            <label>
              设备 host/IP
              <input
                value={form.network_host}
                onChange={(event) => updateForm("network_host", event.target.value)}
                placeholder="例如 laser.local"
              />
            </label>
            <label>
              Telnet 端口
              <input
                type="number"
                min="1"
                step="1"
                value={form.network_telnet_port}
                onChange={(event) => updateForm("network_telnet_port", event.target.value)}
              />
            </label>
            <div className="device-actions">
              <button
                type="button"
                className="secondary-button"
                onClick={testConnection}
                disabled={busy || connectionBusy}
              >
                <Wifi size={16} />
                {connectionBusy ? "测试中..." : "测试连接"}
              </button>
              <button type="button" className="icon-button soft" onClick={saveConfiguration} disabled={busy} aria-label="保存配置">
                <Save size={17} />
              </button>
            </div>
            <ConnectionStatus result={connectionResult} />
          </section>

          <div className="action-dock">
            <button type="button" className="primary-button" onClick={generatePreview} disabled={busy || !excalidrawAPI}>
              <UploadCloud size={18} />
              生成加工预览
            </button>
            <button type="button" className="danger-button" onClick={openSendConfirmation} disabled={busy || !canConfirmSend}>
              <Send size={18} />
              确认发送
            </button>
            {canActOnWorkflow && !hostReady && <div className="inline-hint">请填写设备 host/IP 后再发送</div>}
            {canActOnWorkflow && hostReady && !summaryAllowsSend && (
              <div className="inline-hint">当前预览摘要标记为不可发送</div>
            )}
            <div className="split-actions">
              <button type="button" onClick={() => workflowAction("status")} disabled={busy || !canActOnWorkflow}>
                <Activity size={16} />
                查询状态
              </button>
              <button type="button" onClick={() => workflowAction("cancel")} disabled={busy || !canActOnWorkflow}>
                <Square size={16} />
                取消任务
              </button>
            </div>
            <div className="workflow-chip">
              <RefreshCw size={15} />
              <div>
                <span>WORKFLOW ID</span>
                <strong>{workflowId || "尚未生成"}</strong>
              </div>
            </div>
          </div>
        </aside>

        <section className="canvas-panel">
          <div className="canvas-heading">
            <div>
              <span className="section-index">02</span>
              <div>
                <span className="eyebrow">DESIGN CANVAS</span>
                <h2>创作画布</h2>
              </div>
            </div>
            <div className="canvas-meta">
              <span>Excalidraw</span>
              <span>最大 120 × 120 mm</span>
            </div>
          </div>
          <div className="canvas-stage">
            <Excalidraw excalidrawAPI={setExcalidrawAPI} theme="light" langCode={EXCALIDRAW_LANG_CODE} />
          </div>
        </section>

        <aside className="result-panel">
          <div className="panel-heading result-heading">
            <div>
              <span className="section-index">03</span>
              <div>
                <span className="eyebrow">OUTPUT & REVIEW</span>
                <h2>预览与检查</h2>
              </div>
            </div>
            <ShieldAlert size={19} />
          </div>

          <JobSummaryCard summary={jobSummary} />

          <div className="preview-grid">
            <PreviewImage title="素材预览" url={web.material_preview_url || web.uploaded_image_url} />
            <PreviewImage title="路径预览" url={web.gcode_preview_url || web.image_preview_url} />
          </div>

          <div className="link-list">
            {web.gcode_download_url && (
              <a href={web.gcode_download_url} target="_blank" rel="noreferrer">
                <Save size={16} />
                下载 G-code
              </a>
            )}
            {web.scene_download_url && (
              <a href={web.scene_download_url} target="_blank" rel="noreferrer">
                <FileJson size={16} />
                下载场景 JSON
              </a>
            )}
          </div>
          <details className="diagnostics">
            <summary className="diagnostics-title">
              <AlertTriangle size={16} />
              诊断输出
            </summary>
            <pre>{rawText || "等待预览结果..."}</pre>
          </details>
        </aside>
      </section>
      {confirmOpen && (
        <SendConfirmDialog
          summary={jobSummary}
          host={form.network_host}
          telnetPort={form.network_telnet_port}
          busy={busy}
          onCancel={() => setConfirmOpen(false)}
          onConfirm={confirmSend}
        />
      )}
    </main>
  );
}

function JobSummaryCard({ summary, compact = false }) {
  if (!summary) {
    return (
      <section className="job-summary-card muted-summary">
        <div className="summary-heading">
          <Settings2 size={17} />
          <h2>作业摘要</h2>
        </div>
        <p>生成预览后显示材料、尺寸、参数和预计时间。</p>
      </section>
    );
  }

  const displayWarnings = summaryDisplayWarnings(summary, formatWithUnit);
  const thicknessRows = thicknessDisplayRows(summary);
  const sizeText =
    summary.actual_width_mm && summary.actual_height_mm
      ? `${formatMm(summary.actual_width_mm)} x ${formatMm(summary.actual_height_mm)}`
      : "自动";
  return (
    <section className={`job-summary-card ${compact ? "compact" : ""}`}>
      <div className="summary-heading">
        <Settings2 size={17} />
        <h2>作业摘要</h2>
        <span className={summary.can_send ? "summary-badge ok" : "summary-badge warn"}>
          {sendBadgeLabel(summary)}
        </span>
      </div>
      <div className="summary-grid">
        <SummaryItem label="材料" value={summary.material || "未识别"} />
        {thicknessRows.map((row) => (
          <SummaryItem key={row.label} label={row.label} value={formatWithUnit(summary[row.valueKey], "mm")} />
        ))}
        <SummaryItem label="策略" value={modeLabel(summary.mode, summary.task_type)} />
        <SummaryItem label="尺寸" value={sizeText} icon={<Ruler size={15} />} />
        <SummaryItem label="预计时间" value={summary.estimated_display || "待估算"} icon={<Clock size={15} />} />
        <SummaryItem label="功率" value={summary.power ? `S${summary.power}` : "材料库默认"} />
        <SummaryItem label="速度" value={summary.speed ? `F${summary.speed}` : "材料库默认"} />
        <SummaryItem label="次数" value={summary.passes || "默认"} />
        <SummaryItem label="点距" value={formatWithUnit(summary.pixel_size_mm, "mm")} />
      </div>
      {summary.size_note && <p className="size-note">{summary.size_note}</p>}
      {displayWarnings.length > 0 && (
        <div className="warning-list">
          <ShieldAlert size={16} />
          <span>{displayWarnings.join("；")}</span>
        </div>
      )}
    </section>
  );
}

function SummaryItem({ label, value, icon = null }) {
  return (
    <div className="summary-item">
      <span>{label}</span>
      <strong>
        {icon}
        {value || "未知"}
      </strong>
    </div>
  );
}

function ConnectionStatus({ result }) {
  if (!result) return null;
  const payload = result.result && typeof result.result === "object" ? result.result : {};
  const connected = Boolean(payload.network_connected);
  const text = payload.summary || result.result || "连接检查失败";
  return (
    <div className={`connection-status ${result.success && connected ? "ok" : "warn"}`}>
      {result.success && connected ? <CheckCircle2 size={16} /> : <AlertTriangle size={16} />}
      <span>{text}</span>
    </div>
  );
}

function SendConfirmDialog({ summary, host, telnetPort, busy, onCancel, onConfirm }) {
  // Warnings live only inside JobSummaryCard so nearest-thickness is not repeated here.
  return (
    <div className="modal-backdrop" role="presentation">
      <section className="send-confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="send-confirm-title">
        <div className="confirm-heading">
          <div>
            <Send size={18} />
            <h2 id="send-confirm-title">确认开始雕刻</h2>
          </div>
          <button type="button" className="icon-button" onClick={onCancel} aria-label="关闭确认面板">
            <X size={18} />
          </button>
        </div>
        <JobSummaryCard summary={summary} compact />
        <div className="confirm-device">
          <Wifi size={17} />
          <span>设备：{host}:{telnetPort || "23"}</span>
        </div>
        <div className="confirm-actions">
          <button type="button" className="secondary-button" onClick={onCancel} disabled={busy}>
            取消
          </button>
          <button type="button" className="danger-button" onClick={onConfirm} disabled={busy || !host}>
            <Send size={18} />
            {busy ? "发送中..." : "确认发送"}
          </button>
        </div>
      </section>
    </div>
  );
}

function PreviewImage({ title, url }) {
  return (
    <div className="preview-box">
      <div className="preview-title">{title}</div>
      {url ? <img src={cacheBust(url)} alt={title} /> : <div className="preview-empty">暂无</div>}
    </div>
  );
}

function taskTypeForMode(mode) {
  if (mode === "raster") return "engrave_photo";
  if (mode === "outline") return "engrave_logo";
  return "";
}

function blobToDataUrl(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error || new Error("读取 PNG 失败"));
    reader.readAsDataURL(blob);
  });
}

async function postJson(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.result || `HTTP ${response.status}`);
  }
  return data;
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

function cacheBust(url) {
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}t=${Date.now()}`;
}

function modeLabel(mode, taskType) {
  if (mode === "raster" || taskType === "engrave_photo") return "光栅雕刻";
  if (mode === "outline" || taskType === "engrave_logo") return "轮廓雕刻";
  if (taskType === "cut_contour") return "轮廓切割";
  return mode || taskType || "自动";
}

function formatMm(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "未知";
  return `${Number(number.toFixed(2)).toString()} mm`;
}

function formatWithUnit(value, unit) {
  if (value === undefined || value === null || value === "") return "默认";
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value);
  return `${Number(number.toFixed(3)).toString()} ${unit}`;
}

createRoot(document.getElementById("root")).render(<App />);
