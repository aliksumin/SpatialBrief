import React, { useRef, useState, useEffect } from 'react';
import { Upload, Check, FileText, Loader, Play, AlertTriangle, XCircle, Download, Activity, DollarSign } from 'lucide-react';

interface NodeGraphProps {
  onFilesUploaded: (data: any) => void;
  onNodeChange?: (nodeId: number) => void;
  selectedNode: number | null;
  onSelectNode?: (nodeId: number | null) => void;
  activeNode: number;
  fileCount?: number;
  extractionData?: any;
}

const NODES = [
  { id: 1, title: 'Load Input Bundle', desc: 'Upload documents and CAD vectors', metrics: { files: 2 } },
  { id: 2, title: 'Classify Documents', desc: 'Determine file roles via AI', metrics: { roles: 2, conf: '98%' } },
  { id: 3, title: 'Extract Programme', desc: 'Programme, GFA targets, metadata — formulate extraction tasks', metrics: { labels: 0, buildings: 0, uses: 0 } },
  { id: 4, title: 'Detect Units & Coordinates', desc: 'Detect drawing scale, units and origin point', metrics: { unit: 'meters', crs: 'Local' } },
  { id: 5, title: 'Extract Constraints', desc: 'AI-powered setback, height, density & GFA rule extraction', metrics: { constraints: 0, ai_sourced: 0 } },
  { id: 6, title: 'Extract Vector Geometry', desc: 'Multi-agent ensemble extraction of zones, buildings and boundaries', metrics: { polygons: 0, open: 0, layers: 0 } },
  { id: 7, title: 'Generate Volumes', desc: '3D floor-by-floor volumes, plinths & parking', metrics: { volumes: 0, floors: 0 } },
  { id: 8, title: 'Validation Report', desc: 'Summary of process and cost', metrics: { calls: 0, tokens: 0, cost: '$0.00' } },
  { id: 9, title: 'Export Package', desc: 'Rhino .3dm with sublayer hierarchy & User Attributes', metrics: {} }
];

export const NodeGraph: React.FC<NodeGraphProps> = ({ onFilesUploaded, onNodeChange, selectedNode, onSelectNode, activeNode, fileCount, extractionData }) => {
  const [isUploading, setIsUploading] = useState(false);
  const [isProcessing, setIsProcessing] = useState(false);
  const [uploadResult, setUploadResult] = useState<any>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollContainerRef.current) {
      const activeNodeEl = scrollContainerRef.current.querySelector('.node-item.active');
      if (activeNodeEl) {
        activeNodeEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
    }
  }, [activeNode]);

  const handleFileUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = event.target.files;
    if (!files || files.length === 0) return;

    setIsUploading(true);
    const formData = new FormData();
    for (let i = 0; i < files.length; i++) {
      formData.append('files', files[i]);
    }

    try {
      // Node 1: upload-only endpoint — just saves files, no processing
      const response = await fetch('http://localhost:8200/api/v1/upload', {
        method: 'POST',
        body: formData,
      });
      const data = await response.json();
      setUploadResult(data);
      onFilesUploaded(data);
      if(onNodeChange) onNodeChange(2);
    } catch (error) {
      console.error('Error uploading files:', error);
      alert('Failed to connect to backend API. Is it running?');
    } finally {
      setIsUploading(false);
    }
  };

  const startPipeline = async () => {
    setIsProcessing(true);
    const filenames = extractionData?.filenames || uploadResult?.filenames || [];
    if (filenames.length === 0) {
      alert('No files uploaded. Please load files first.');
      setIsProcessing(false);
      return;
    }

    // Node 2: Classify Documents
    if(onNodeChange) onNodeChange(2);
    await new Promise(r => setTimeout(r, 400));

    // Node 3: Start processing — stream progress from backend
    if(onNodeChange) onNodeChange(3);

    try {
      const headers: Record<string, string> = { 'Content-Type': 'application/json' };
      const geminiKey = localStorage.getItem('geminiKey')?.trim();
      if (geminiKey) headers['X-Gemini-Api-Key'] = geminiKey;
      const geminiModel = localStorage.getItem('googleModel')?.trim();
      if (geminiModel) headers['X-Gemini-Model'] = geminiModel;
      const agentVisual = localStorage.getItem('agentVisualModel')?.trim();
      if (agentVisual) headers['X-Agent-Visual-Model'] = agentVisual;
      const agentGeometric = localStorage.getItem('agentGeometricModel')?.trim();
      if (agentGeometric) headers['X-Agent-Geometric-Model'] = agentGeometric;
      const agentContextual = localStorage.getItem('agentContextualModel')?.trim();
      if (agentContextual) headers['X-Agent-Contextual-Model'] = agentContextual;
      const agentJudge = localStorage.getItem('agentJudgeModel')?.trim();
      if (agentJudge) headers['X-Agent-Judge-Model'] = agentJudge;

      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 600_000);

      let response: Response;
      try {
        response = await fetch('http://localhost:8200/api/v1/process', {
          method: 'POST',
          headers,
          body: JSON.stringify({ filenames }),
          signal: controller.signal,
        });
      } finally {
        clearTimeout(timeoutId);
      }

      if (!response.ok) {
        const errText = await response.text().catch(() => '');
        throw new Error(`Server error ${response.status}: ${errText.slice(0, 200)}`);
      }

      // Read NDJSON stream — each line is {"node": N, "result": {...}}
      const reader = response.body!.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop()!; // keep incomplete trailing line

        for (const line of lines) {
          if (!line.trim()) continue;
          try {
            const event = JSON.parse(line);
            if (event.node === 'done') {
              // Pipeline complete — advance to Export
              if(onNodeChange) onNodeChange(9);
            } else if (typeof event.node === 'number' && event.result) {
              // Node completed — show its data immediately
              onFilesUploaded(event.result);
              // Advance to the NEXT node (current one is done)
              if(onNodeChange) onNodeChange(event.node + 1);
            }
          } catch (parseErr) {
            console.warn('Failed to parse stream event:', line, parseErr);
          }
        }
      }

      // Process any remaining buffer
      if (buffer.trim()) {
        try {
          const event = JSON.parse(buffer);
          if (event.result) onFilesUploaded(event.result);
          if (event.node === 'done') {
            if(onNodeChange) onNodeChange(9);
          } else if (typeof event.node === 'number') {
            if(onNodeChange) onNodeChange(event.node + 1);
          }
        } catch {}
      }

    } catch (error: any) {
      console.error('Pipeline processing failed:', error);
      if (error?.name === 'AbortError') {
        alert('Processing timed out (600s). The AI analysis may be taking too long.\n\nTry:\n• Using a faster model (e.g. gemini-2.5-flash)\n• Processing without an API key (rule-based mode)\n• Uploading a smaller document');
      } else {
        alert(`Processing failed: ${error?.message || 'Unknown error'}.\nCheck if the backend is running.`);
      }
    }

    setIsProcessing(false);
  };

  const handleExport = async () => {
    try {
      setIsProcessing(true);

      const geometry = extractionData?.geometry?.raw_vector_objects || [];
      const constraints = extractionData?.geometry?.constraints || [];

      if (geometry.length === 0) {
        alert('No geometry data to export. Please process files first.');
        setIsProcessing(false);
        return;
      }

      const response = await fetch('http://localhost:8200/api/v1/export/rhino', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          geometry,
          constraints,
          project_name: 'SpatialBrief Export',
          format: '3dm',
        }),
      });

      if (!response.ok) {
        throw new Error(`Export failed: ${response.statusText}`);
      }

      const disposition = response.headers.get('Content-Disposition') || '';
      const filenameMatch = disposition.match(/filename="?([^"]+)"?/);
      const filename = filenameMatch ? filenameMatch[1] : 'zoning_massing.3dm';
      const is3dm = filename.endsWith('.3dm');

      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);

      if(onNodeChange) onNodeChange(9);
      setIsProcessing(false);

      const formatName = is3dm ? '.3dm (Rhino)' : '.dxf (Rhino-compatible)';
      alert(`Export complete!\n\nDownloaded:\n• ${filename} — ${formatName}\n\nLayer hierarchy with sublayers and Rhino User Attributes are embedded in the file.\nOpen in Rhino to see all layers, object names, and attributes.`);

    } catch (err: any) {
      console.error('Export failed:', err);
      alert(`Export failed: ${err?.message || 'Unknown error'}.\nCheck if the backend is running.`);
      setIsProcessing(false);
    }
  };

  return (
    <div className="node-container" ref={scrollContainerRef} style={{ paddingBottom: '4rem' }} onClick={() => onSelectNode?.(null)}>
      {NODES.map((node) => {
        const isDone = activeNode > node.id;
        const isActive = activeNode === node.id;
        const isPending = activeNode < node.id;
        
        let statusColor = 'var(--border-color)';
        if (isDone) statusColor = 'var(--node-done)';
        if (isActive) statusColor = 'var(--accent-color)';
        if (isDone && (node as any).error) statusColor = 'var(--node-error, #ef4444)';
        else if (isDone && (node as any).warning) statusColor = 'var(--node-warning, #f59e0b)';
        
        const isSelected = selectedNode === node.id;

        return (
          <div 
            key={node.id} 
            className={`node-item ${isActive ? 'active' : ''} ${isDone ? 'done' : ''} ${isSelected ? 'selected' : ''}`} 
            style={{ opacity: isPending && node.id !== 1 ? 0.4 : 1, cursor: isDone || isActive ? 'pointer' : 'default' }}
            onClick={(e) => { 
                e.stopPropagation();
                if ((isDone || isActive) && onSelectNode) onSelectNode(node.id); 
            }}
          >
            <div className="node-indicator" style={{ 
              borderColor: isSelected ? '#fff' : statusColor,
              background: isDone ? statusColor : isActive ? 'rgba(124,92,252,0.12)' : 'transparent',
              color: isDone ? '#fff' : isActive ? statusColor : 'var(--text-secondary)',
              boxShadow: isSelected ? `0 0 12px rgba(124,92,252,0.5)` : isDone ? `0 0 8px ${statusColor}40` : isActive ? `0 0 10px rgba(124,92,252,0.25)` : 'none',
              transform: isSelected ? 'scale(1.1)' : 'scale(1)'
            }}>
              {isActive && isProcessing ? <Loader size={14} className="animate-spin" /> : node.id}
            </div>
            
            <div className="node-content" style={{ 
              borderColor: isSelected ? 'rgba(124,92,252,0.5)' : isDone ? `${statusColor}30` : isActive ? 'rgba(124,92,252,0.25)' : undefined,
              boxShadow: isSelected ? `0 0 20px rgba(124,92,252,0.15)` : isActive ? `0 0 16px rgba(124,92,252,0.08)` : 'none',
              background: isActive ? 'rgba(124,92,252,0.04)' : isSelected ? 'rgba(124,92,252,0.06)' : undefined
            }}>
              <div className="node-title">
                {node.title}
                {isActive && isProcessing && <Activity size={14} className="animate-pulse" style={{ color: statusColor }} />}
              </div>
              <div className="node-desc">{node.desc}</div>
              
              {/* Data Badges */}
              {isDone && node.metrics && Object.keys(node.metrics).length > 0 && (
                <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', marginTop: '0.75rem' }}>
                  {Object.entries(node.metrics).map(([k, v]) => {
                    let displayValue = String(v);
                    if (fileCount !== undefined) {
                      if (node.id === 1 && k === 'files') displayValue = String(fileCount);
                      if (node.id === 2 && k === 'roles') displayValue = String(fileCount);
                    }
                    if (extractionData) {
                      const geo = extractionData.geometry || extractionData;
                      const vecs = geo?.raw_vector_objects || [];
                      const texts = geo?.extracted_text || [];
                      const csts = geo?.constraints || [];
                      const progs = geo?.programmes || [];
                      const vols = geo?.volumes || [];
                      // Node 3 — Extract Programme
                      if (node.id === 3 && k === 'labels') displayValue = String(texts.length);
                      if (node.id === 3 && k === 'buildings') displayValue = String(progs.length);
                      if (node.id === 3 && k === 'uses') {
                        const allUses = new Set(progs.flatMap((p: any) => (p.uses || []).map((u: any) => u.use)));
                        displayValue = String(allUses.size);
                      }
                      // Node 5 — Extract Vector Geometry
                      if (node.id === 5 && k === 'polygons') displayValue = String(vecs.filter((v: any) => v.closed).length);
                      if (node.id === 5 && k === 'open') displayValue = String(vecs.filter((v: any) => !v.closed).length);
                      if (node.id === 5 && k === 'layers') {
                        const types = new Set(vecs.map((v: any) => v.zone_type));
                        displayValue = String(types.size);
                      }
                      // Node 6 — Extract Constraints
                      if (node.id === 6 && k === 'constraints') displayValue = String(csts.length);
                      if (node.id === 6 && k === 'ai_sourced') displayValue = String(csts.filter((c: any) => c.source === 'ai_extracted' || c.source === 'ai_suggested').length);
                      // Node 7 — Generate Volumes
                      if (node.id === 7 && k === 'volumes') displayValue = String(vols.length);
                      if (node.id === 7 && k === 'floors') {
                        displayValue = String(vols.filter((v: any) => v.zone_type === 'building_floor').length);
                      }
                      // Node 8 — Validation Report (Cost)
                      const costData = geo?.cost_summary || extractionData?.cost_summary;
                      if (node.id === 8 && k === 'calls') displayValue = String(costData?.total_calls || 0);
                      if (node.id === 8 && k === 'tokens') {
                        const t = costData?.total_tokens || 0;
                        displayValue = t > 1000 ? `${(t/1000).toFixed(1)}k` : String(t);
                      }
                      if (node.id === 8 && k === 'cost') {
                        const c = costData?.estimated_cost_usd || 0;
                        displayValue = c > 0 ? `$${c.toFixed(4)}` : '$0.00';
                      }
                    }
                    return (
                      <div key={k} style={{ background: 'rgba(255,255,255,0.03)', padding: '0.15rem 0.4rem', borderRadius: '4px', fontSize: '0.65rem', color: 'var(--text-secondary)', border: '1px solid rgba(255,255,255,0.04)' }}>
                        <span style={{ color: 'var(--text-primary)', fontWeight: 600 }}>{displayValue}</span> {k}
                      </div>
                    );
                  })}
                </div>
              )}

              {/* Warnings and Errors */}
              {isDone && (node as any).warning && (
                <div style={{ marginTop: '0.75rem', fontSize: '0.75rem', color: 'var(--node-warning, #f59e0b)', display: 'flex', alignItems: 'center', gap: '0.25rem' }}>
                  <AlertTriangle size={14} /> {(node as any).warning}
                </div>
              )}
              {isDone && (node as any).error && (
                <div style={{ marginTop: '0.75rem', fontSize: '0.75rem', color: 'var(--node-error, #ef4444)', display: 'flex', alignItems: 'center', gap: '0.25rem' }}>
                  <XCircle size={14} /> {(node as any).error}
                </div>
              )}

              {/* Node 8 — Cost breakdown */}
              {node.id === 8 && isDone && (() => {
                const costData = (extractionData?.geometry || extractionData)?.cost_summary || extractionData?.cost_summary;
                if (!costData || !costData.calls || costData.calls.length === 0) return null;
                return (
                  <div style={{
                    marginTop: '0.75rem', padding: '0.6rem',
                    background: 'rgba(16,185,129,0.04)', borderRadius: '8px',
                    border: '1px solid rgba(16,185,129,0.12)'
                  }}>
                    <div style={{ fontSize: '0.65rem', color: 'var(--text-secondary)', marginBottom: '0.4rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                      <DollarSign size={10} style={{ display: 'inline', verticalAlign: 'middle', marginRight: '0.2rem' }} />
                      Cost Breakdown
                    </div>
                    {costData.calls.map((call: any, i: number) => (
                      <div key={i} style={{
                        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                        fontSize: '0.6rem', color: 'var(--text-secondary)',
                        padding: '0.15rem 0', borderBottom: i < costData.calls.length - 1 ? '1px solid rgba(255,255,255,0.03)' : 'none'
                      }}>
                        <span style={{ opacity: 0.7 }}>{call.stage.replace(/_/g, ' ')}</span>
                        <span style={{ fontFamily: 'monospace', fontSize: '0.6rem' }}>
                          {call.input_tokens + call.output_tokens > 1000
                            ? `${((call.input_tokens + call.output_tokens)/1000).toFixed(1)}k tok`
                            : `${call.input_tokens + call.output_tokens} tok`
                          }
                        </span>
                      </div>
                    ))}
                    <div style={{
                      display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                      marginTop: '0.4rem', paddingTop: '0.4rem',
                      borderTop: '1px solid rgba(16,185,129,0.15)',
                      fontSize: '0.7rem', fontWeight: 600, color: '#10b981'
                    }}>
                      <span>Total</span>
                      <span>${costData.estimated_cost_usd?.toFixed(4) || '0.0000'}</span>
                    </div>
                  </div>
                );
              })()}

              {/* Node 1 Upload Action */}
              {node.id === 1 && !isDone && isActive && (
                <div className="file-upload" onClick={() => fileInputRef.current?.click()} style={{ marginTop: '0.5rem' }}>
                  {isUploading ? (
                    <Loader className="animate-spin" size={24} style={{ margin: '0 auto' }} />
                  ) : (
                    <>
                      <Upload size={20} color="var(--text-secondary)" style={{ margin: '0 auto 0.4rem' }} />
                      <div style={{ fontSize: '0.8rem' }}>Load files</div>
                    </>
                  )}
                  <input type="file" ref={fileInputRef} onChange={handleFileUpload} style={{ display: 'none' }} multiple />
                </div>
              )}

              {/* Node 1 — show loaded file names after upload */}
              {node.id === 1 && isDone && (() => {
                const fnames = extractionData?.filenames || extractionData?.geometry?.source_files || [];
                if (fnames.length === 0) return null;
                return (
                  <div style={{ marginTop: '0.4rem', display: 'flex', flexDirection: 'column', gap: '0.2rem' }}>
                    {fnames.map((f: string, i: number) => (
                      <div key={i} style={{
                        display: 'flex', alignItems: 'center', gap: '0.4rem',
                        background: 'rgba(255,255,255,0.03)', padding: '0.3rem 0.5rem',
                        borderRadius: '6px', border: '1px solid rgba(255,255,255,0.04)',
                        fontSize: '0.7rem', color: 'var(--text-primary)',
                      }}>
                        <FileText size={12} style={{ flexShrink: 0, color: 'var(--accent-color)' }} />
                        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1, minWidth: 0 }}>{f}</span>
                      </div>
                    ))}
                  </div>
                );
              })()}

              {/* Node 2 Trigger */}
              {node.id === 2 && isActive && !isProcessing && (
                <button className="btn" onClick={startPipeline} style={{ marginTop: '1rem', width: '100%', background: 'var(--accent-color)' }}>
                  <Play size={16} /> Run Inference Engine
                </button>
              )}

              {/* Node 9 Manual Export */}
              {node.id === 9 && isActive && !isProcessing && (
                <button className="btn" onClick={handleExport} style={{ marginTop: '1rem', width: '100%', background: '#10b981' }}>
                  <Download size={16} /> Export Rhino/GH Package
                </button>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
};
