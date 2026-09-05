import React, { useEffect, useMemo, useRef, useState } from "react";
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
  summaryAllowsConfirmSend,
  summaryDisplayWarnings,
  thicknessDisplayRows,
} from "./jobSummaryView.js";
import {
  CUSTOM_OPTION_LABEL,
  CUSTOM_OPTION_VALUE,
  applyMaterialLibraryRefresh,
  applyMaterialSelectChange,
  findMaterialOption,
  loadUiMaterialOptions,
  restoreMaterialSelectState,
  returnToLibrary,
} from "./materialSelectState.js";
import { getLaserExportDimensions } from "./laserExportDimensions.js";
import {
  applyConfirmSendResponse,
  applyPreviewFingerprintSuccess,
  canSubmitConfirmSend,
  createRepeatSendState,
  requiresMovedMaterialAcknowledgement,
  setMovedMaterialAcknowledged,
} from "./repeatSendGuard.js";
import "./styles.css";

const CONFIG_KEYS = [
  "material",
  "thickness_mm",
  "width_mm",
  "height_mm",
  "mode",
  "network_host",
  "network_telnet_port",
  "vector_simplify_factor",
];
const EXCALIDRAW_LANG_CODE = "zh-CN";
const MATERIAL_CHANNEL_NAME = "laser-material-library-v1";

const DEFAULT_FORM = {
  material: localStorage.getItem("laserLab.material") || "椴木",
  thickness_mm: localStorage.getItem("laserLab.thickness_mm") || "3",
  width_mm: localStorage.getItem("laserLab.width_mm") || "",
  height_mm: localStorage.getItem("laserLab.height_mm") || "",
  mode: localStorage.getItem("laserLab.mode") || "raster",
  network_host: localStorage.getItem("laserLab.network_host") || "",
  network_telnet_port: localStorage.getItem("laserLab.network_telnet_port") || "23",
  vector_simplify_factor: localStorage.getItem("laserLab.vector_simplify_factor") || "3",
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
  const [materialRefreshBusy, setMaterialRefreshBusy] = useState(false);
  const [repeatSend, setRepeatSend] = useState(() => createRepeatSendState(window.localStorage));
  const materialLibraryRef = useRef(materialLibrary);
  const materialModeRef = useRef(materialMode);
  const formRef = useRef(form);
  const materialLoadedOnceRef = useRef(false);
  const materialRefreshGenerationRef = useRef(0);

  useEffect(() => {
    materialLibraryRef.current = materialLibrary;
  }, [materialLibrary]);
  useEffect(() => {
    materialModeRef.current = materialMode;
  }, [materialMode]);
  useEffect(() => {
    formRef.current = form;
  }, [form]);

  const web = result?.result?.web || {};
  const lab = result?.result?.draw_lab || {};
  const jobSummary = lab.job_summary || null;
  const rawText = useMemo(() => (result ? JSON.stringify(result, null, 2) : ""), [result]);
  const canActOnWorkflow = Boolean(workflowId);
  const hostReady = Boolean(form.network_host.trim());
  const nextActions = Array.isArray(result?.result?.next_actions) ? result.result.next_actions : [];
  const summaryAllowsSend = summaryAllowsConfirmSend(jobSummary, nextActions);
  const canConfirmSend = canActOnWorkflow && hostReady && summaryAllowsSend;

  function updateForm(key, value) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  async function refreshMaterialLibrary() {
    const generation = ++materialRefreshGenerationRef.current;
    setMaterialRefreshBusy(true);
    try {
      const currentForm = formRef.current;
      const loaded = await loadUiMaterialOptions(fetch);
      if (generation !== materialRefreshGenerationRef.current) return;
      const decision = applyMaterialLibraryRefresh({
        loaded,
        currentMode: materialModeRef.current,
        currentMaterial: currentForm.material,
        currentThickness: currentForm.thickness_mm,
        previousMaterials: materialLibraryRef.current,
        hasLoadedOnce: materialLoadedOnceRef.current,
      });
      if (!decision.applied) {
        return;
      }
      if (loaded.ok) {
        materialLoadedOnceRef.current = true;
      }
      setMaterialLibrary(decision.materials);
      setMaterialMode(decision.mode);
      setMaterialSelectValue(decision.selectValue);
      const nextForm = {
        ...currentForm,
        material: decision.material || currentForm.material,
        thickness_mm: decision.thickness_mm || currentForm.thickness_mm,
      };
      setForm((current) => ({
        ...current,
        material: nextForm.material,
        thickness_mm: nextForm.thickness_mm,
      }));
    } finally {
      if (generation === materialRefreshGenerationRef.current) {
        setMaterialRefreshBusy(false);
      }
    }
  }

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const generation = ++materialRefreshGenerationRef.current;
      const loaded = await loadUiMaterialOptions(fetch);
      if (cancelled || generation !== materialRefreshGenerationRef.current) return;
      const restored = restoreMaterialSelectState({
        materials: loaded.materials,
        savedMaterial: form.material,
        savedThickness: form.thickness_mm,
      });
      if (loaded.ok) {
        materialLoadedOnceRef.current = true;
      }
      setMaterialLibrary(restored.materials);
      setMaterialMode(restored.mode);
      setMaterialSelectValue(restored.selectValue);
      const nextForm = {
        ...form,
        material: restored.material || form.material,
        thickness_mm: restored.thickness_mm || form.thickness_mm,
      };
      setForm((current) => ({
        ...current,
        material: nextForm.material,
        thickness_mm: nextForm.thickness_mm,
      }));
    })();
    return () => {
      cancelled = true;
    };
    // Intentionally run once on mount to restore from localStorage defaults.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    function onFocus() {
      refreshMaterialLibrary();
    }
    function onVisibility() {
      if (document.visibilityState === "visible") {
        refreshMaterialLibrary();
      }
    }
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onVisibility);
    let channel = null;
    try {
      if (typeof BroadcastChannel === "function") {
        channel = new BroadcastChannel(MATERIAL_CHANNEL_NAME);
        channel.addEventListener("message", (event) => {
          if (event && event.data && event.data.type === "materials-updated") {
            refreshMaterialLibrary();
          }
        });
      }
    } catch (_error) {
      channel = null;
    }
    return () => {
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onVisibility);
      if (channel) {
        try {
          channel.close();
        } catch (_error) {
          // ignore close failures
        }
      }
    };
    // Stable lifecycle listeners; refresh helpers read latest state via refs.
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
      const value = form[key];
      if (typeof value === "boolean") {
        localStorage.setItem(`laserLab.${key}`, value ? "true" : "false");
      } else {
        localStorage.setItem(`laserLab.${key}`, value ?? "");
      }
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
      // Laser preview only: inject high-res cap; ordinary export leaves getDimensions unset.
      ...(forLaser ? { getDimensions: getLaserExportDimensions } : {}),
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
        // Always lock aspect ratio when both width/height are set (fit inside bounds).
        lock_aspect_ratio: true,
      };
      if (form.mode === "raster") {
        payload.dither_algorithm = "threshold";
        payload.raster_scan_direction = "horizontal";
        payload.raster_output_strategy = "scanline";
        payload.raster_quality_strategy = "manual";
      }
      if (form.mode === "outline") {
        const simplifyRaw = String(form.vector_simplify_factor ?? "").trim();
        payload.vector_simplify_factor = simplifyRaw === "" ? 3 : Number(simplifyRaw);
      }
      const data = await postJson("/api/draw/preview", payload);
      setResult(data);
      const nextWorkflowId = data?.result?.workflow_id || data?.result?.workflow?.workflow_id || "";
      setWorkflowId(nextWorkflowId);
      setConfirmOpen(false);
      if (data.success) {
        const fingerprint = data?.result?.draw_lab?.content_fingerprint || "";
        setRepeatSend((current) => applyPreviewFingerprintSuccess(current, fingerprint));
        setStatus(data.result?.speech || "预览已生成");
      } else {
        setStatus(data.result || "预览失败");
      }
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
      setStatus("当前预览不可发送：材料参数未验证或服务端未提供 confirm_send");
      return;
    }
    if (!canSubmitConfirmSend(repeatSend)) {
      setStatus("相同图案再次发送前，请勾选“已移动材料或原点”");
    }
    setConfirmOpen(true);
  }

  async function confirmSend() {
    if (!workflowId || !hostReady) {
      setStatus("请先生成预览并填写设备 host/IP");
      return;
    }
    if (!summaryAllowsSend) {
      setStatus("当前预览不可发送：材料参数未验证或服务端未提供 confirm_send");
      return;
    }
    if (!canSubmitConfirmSend(repeatSend)) {
      setStatus("相同图案再次发送前，请勾选“已移动材料或原点”");
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
      setRepeatSend((current) => applyConfirmSendResponse(current, data, window.localStorage));
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
      <section className="topbar">
        <div>
          <h1>Excalidraw Laser Lab</h1>
          <p>{status}</p>
        </div>
        <div className="topbar-actions">
          <button type="button" className="ghost-button" onClick={goHome}>
            <Home size={18} />
            首页
          </button>
          <button type="button" className="ghost-button" onClick={downloadScene} disabled={busy || !excalidrawAPI}>
            <FileJson size={18} />
            场景
          </button>
          <button type="button" className="ghost-button" onClick={downloadPng} disabled={busy || !excalidrawAPI}>
            <Download size={18} />
            PNG
          </button>
        </div>
      </section>

      <section className="workspace">
        <aside className="control-panel">
          <div className="material-library-label">
            <span>材料库</span>
            <div className="material-library-row">
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
              <button
                type="button"
                className="ghost-button icon-button"
                title="刷新材料库"
                aria-label="刷新材料库"
                disabled={materialRefreshBusy}
                onClick={() => {
                  refreshMaterialLibrary();
                }}
              >
                <RefreshCw size={16} />
              </button>
            </div>
          </div>
          {materialMode === "custom" ? (
            <label>
              材料名称
              <input
                value={form.material}
                onChange={(event) => updateForm("material", event.target.value)}
              />
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
            <button type="button" className="ghost-button" onClick={handleReturnToLibrary}>
              返回材料库
            </button>
          ) : null}
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
              <option value="raster">光栅雕刻 raster</option>
              <option value="outline">轮廓雕刻 outline</option>
              <option value="auto">自动判断 auto</option>
            </select>
          </label>
          {form.mode === "outline" ? (
            <label>
              轮廓简化 vector_simplify_factor
              <input
                type="number"
                min="0.25"
                max="8"
                step="0.05"
                value={form.vector_simplify_factor}
                onChange={(event) => updateForm("vector_simplify_factor", event.target.value)}
                placeholder="默认 3"
              />
            </label>
          ) : null}
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
          <button
            type="button"
            className="secondary-button"
            onClick={testConnection}
            disabled={busy || connectionBusy}
          >
            <Wifi size={17} />
            {connectionBusy ? "测试中..." : "测试连接"}
          </button>
          <ConnectionStatus result={connectionResult} />

          <button type="button" className="secondary-button" onClick={saveConfiguration} disabled={busy}>
            <Save size={17} />
            保存配置
          </button>

          <div className="action-stack">
            <button type="button" className="primary-button" onClick={generatePreview} disabled={busy || !excalidrawAPI}>
              <UploadCloud size={18} />
              生成预览
            </button>
            <button type="button" className="danger-button" onClick={openSendConfirmation} disabled={busy || !canConfirmSend}>
              <Send size={18} />
              确认发送
            </button>
            {canActOnWorkflow && !hostReady && <div className="inline-hint">请填写设备 host/IP 后再发送</div>}
            {canActOnWorkflow && hostReady && !summaryAllowsSend && (
              <div className="inline-hint">当前不可发送：请检查精确厚度参数与服务端确认权限</div>
            )}
            <div className="split-actions">
              <button type="button" onClick={() => workflowAction("status")} disabled={busy || !canActOnWorkflow}>
                <Activity size={17} />
                状态
              </button>
              <button type="button" onClick={() => workflowAction("cancel")} disabled={busy || !canActOnWorkflow}>
                <Square size={17} />
                取消
              </button>
            </div>
          </div>

          <div className="workflow-chip">
            <RefreshCw size={16} />
            <span>{workflowId || "暂无 workflow"}</span>
          </div>
        </aside>

        <section className="canvas-panel">
          <Excalidraw excalidrawAPI={setExcalidrawAPI} theme="light" langCode={EXCALIDRAW_LANG_CODE} />
        </section>

        <aside className="result-panel">
          <JobSummaryCard summary={jobSummary} />
          <PreviewImage title="素材预览" url={web.material_preview_url || web.uploaded_image_url} />
          <PreviewImage title="路径预览" url={web.gcode_preview_url || web.image_preview_url} />
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
          sendAllowed={summaryAllowsSend}
          onCancel={() => setConfirmOpen(false)}
          onConfirm={confirmSend}
          requireMovedAck={requiresMovedMaterialAcknowledgement(repeatSend)}
          movedAcknowledged={Boolean(repeatSend.movedMaterialAcknowledged)}
          onMovedAckChange={(value) =>
            setRepeatSend((current) => setMovedMaterialAcknowledged(current, value))
          }
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

function SendConfirmDialog({
  summary,
  host,
  telnetPort,
  busy,
  sendAllowed = false,
  onCancel,
  onConfirm,
  requireMovedAck = false,
  movedAcknowledged = false,
  onMovedAckChange,
}) {
  // Warnings live only inside JobSummaryCard so nearest-thickness is not repeated here.
  const canConfirm = !requireMovedAck || movedAcknowledged;
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
        {requireMovedAck ? (
          <label className="checkbox-row confirm-moved-ack">
            <input
              type="checkbox"
              checked={Boolean(movedAcknowledged)}
              onChange={(event) => onMovedAckChange?.(event.target.checked)}
            />
            已移动材料或原点（相同图案再次发送前必须勾选）
          </label>
        ) : null}
        <div className="confirm-actions">
          <button type="button" className="secondary-button" onClick={onCancel} disabled={busy}>
            取消
          </button>
          <button
            type="button"
            className="danger-button"
            onClick={onConfirm}
            disabled={busy || !host || !sendAllowed || !canConfirm}
          >
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
