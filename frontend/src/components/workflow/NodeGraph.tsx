import React, { useRef, useState, useEffect } from 'react';
import { Upload, Check, FileText, Loader, Play, AlertTriangle, XCircle, Download, Activity } from 'lucide-react';

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
  { id: 3, title: 'Extract Metadata', desc: 'Parse text, tables and annotations', metrics: { entities: 15, labels: 42 } },
  { id: 4, title: 'Detect Units & Coordinates', desc: 'Detect drawing scale and origin point', metrics: { unit: 'meters', crs: 'Local' } },
  { id: 5, title: 'Separate Drawing Areas', desc: 'Analyse plot, envelopes, and no-build zones', metrics: { zones_identified: 3 } },
  { id: 6, title: 'Extract Vector Geometry', desc: 'Reconstruct, close polylines and extract clean zones', metrics: { polygons: 3, open: 0, layers: 3 } },
  { id: 7, title: 'Extract Constraints', desc: 'AI-powered setback, height & rule extraction', metrics: { constraints: 0, ai_sourced: 0 } },
  { id: 8, title: 'Extract Programme', desc: 'GFA, uses, floors per building — AI fills gaps', metrics: { buildings: 0, uses: 0 } },
  { id: 9, title: 'Generate Volumes', desc: '3D floor-by-floor volumes, plinths & parking', metrics: { volumes: 0, floors: 0 } },
  { id: 10, title: 'Validation Report', desc: 'Summary of process', metrics: {} },
  { id: 11, title: 'Export Package', desc: 'Rhino / Grasshopper handoff', metrics: {} }
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
      // Store filenames only (no geometry yet)
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

    // Node 2: Classify Documents (instant — determine file roles)
    if(onNodeChange) onNodeChange(2);
    await new Promise(r => setTimeout(r, 400));

    // Node 3: Extract Metadata + Annotations — triggers the full /process call
    if(onNodeChange) onNodeChange(3);

    try {
      const headers: Record<string, string> = { 'Content-Type': 'application/json' };
      const geminiKey = localStorage.getItem('geminiKey')?.trim();
      if (geminiKey) headers['X-Gemini-Api-Key'] = geminiKey;
      const geminiModel = localStorage.getItem('googleModel')?.trim();
      if (geminiModel) headers['X-Gemini-Model'] = geminiModel;

      // Use AbortController with 300s timeout — the backend runs multiple
      // sequential AI calls (vision + constraints + programme)
      // which can take 60-150s total with powerful models.
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 300_000);

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

      const result = await response.json();

      // Pipeline data received — step through nodes showing progressive results
      // Node 3 done: metadata + annotations extracted
      onFilesUploaded(result);
      await new Promise(r => setTimeout(r, 500));

      // Node 4: Detect Units & Coordinates
      if(onNodeChange) onNodeChange(4);
      await new Promise(r => setTimeout(r, 400));

      // Node 5: Separate Drawing Areas
      if(onNodeChange) onNodeChange(5);
      await new Promise(r => setTimeout(r, 400));

      // Node 6: Extract Vector Geometry (includes reconstruction)
      if(onNodeChange) onNodeChange(6);
      await new Promise(r => setTimeout(r, 500));

      // Nodes 7-11: step through remaining nodes
      for (let i = 7; i <= 11; i++) {
        if(onNodeChange) onNodeChange(i);
        await new Promise(r => setTimeout(r, 400));
      }
    } catch (error: any) {
      console.error('Pipeline processing failed:', error);
      if (error?.name === 'AbortError') {
        alert('Processing timed out (300s). The AI analysis may be taking too long.\n\nTry:\n• Processing without an API key (rule-based mode)\n• Uploading a smaller document');
      } else {
        alert(`Processing failed: ${error?.message || 'Unknown error'}.\nCheck if the backend is running.`);
      }
    }

    setIsProcessing(false);
  };

  const handleExport = async () => {
    try {
      setIsProcessing(true);

      // Gather geometry data from the current project
      const geometry = extractionData?.geometry?.raw_vector_objects || [];
      const constraints = extractionData?.geometry?.constraints || [];

      if (geometry.length === 0) {
        alert('No geometry data to export. Please process files first.');
        setIsProcessing(false);
        return;
      }

      // 1. Export DXF via backend
      const response = await fetch('http://localhost:8200/api/v1/export/rhino', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          geometry,
          constraints,
          project_name: 'SpatialBrief Export',
          format: 'dxf',
        }),
      });

      if (!response.ok) {
        throw new Error(`Export failed: ${response.statusText}`);
      }

      // Download the DXF file
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'zoning_massing.dxf';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);

      // 2. Also export design_inputs.json with real data
      const constraintParams: Record<string, any> = {};
      if (constraints.length > 0) {
        for (const c of constraints) {
          const key = (c.name || c.category || 'constraint').replace(/\s+/g, '_');
          constraintParams[key] = { value: c.value, unit: c.unit, category: c.category };
        }
      }

      const programmes = extractionData?.geometry?.programmes || [];
      const exportData = {
        metadata: {
          project: 'SpatialBrief Export',
          generator: 'OMRT Vector Engine',
          timestamp: new Date().toISOString(),
          total_objects: geometry.length,
          total_constraints: constraints.length,
          classification_mode: extractionData?.classification_mode || 'unknown',
        },
        constraints: constraintParams,
        programmes: programmes.map((p: any) => ({
          building_id: p.building_id,
          building_label: p.building_label,
          floors: p.floors,
          floor_height: p.floor_height,
          total_height: p.total_height,
          uses: p.uses,
        })),
        layer_hierarchy: [
          '00_Boundaries', '01_Zones', '02_Buildings',
          '03_Constraints', '04_Generated_Volumes', '05_Infrastructure',
        ],
      };

      const jsonBlob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' });
      const jsonUrl = URL.createObjectURL(jsonBlob);
      const jsonA = document.createElement('a');
      jsonA.href = jsonUrl;
      jsonA.download = 'design_inputs.json';
      document.body.appendChild(jsonA);
      jsonA.click();
      document.body.removeChild(jsonA);
      URL.revokeObjectURL(jsonUrl);

      if(onNodeChange) onNodeChange(11);
      setIsProcessing(false);
      alert(`Export complete!\n\nDownloaded:\n• zoning_massing.dxf — Layered geometry for Rhino\n• design_inputs.json — Constraints & programme data\n\nOpen the .dxf in Rhino to see all layers and metadata.`);

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
        if (isDone && node.error) statusColor = 'var(--node-error, #ef4444)';
        else if (isDone && node.warning) statusColor = 'var(--node-warning, #f59e0b)';
        
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
                    // Dynamic metrics for extraction nodes
                    if (extractionData) {
                      const geo = extractionData.geometry || extractionData;
                      const vecs = geo?.raw_vector_objects || [];
                      const texts = geo?.extracted_text || [];
                      const csts = geo?.constraints || [];
                      if (node.id === 3 && k === 'labels') displayValue = String(texts.length);
                      if (node.id === 6 && k === 'polygons') displayValue = String(vecs.filter((v: any) => v.closed).length);
                      if (node.id === 6 && k === 'open') displayValue = String(vecs.filter((v: any) => !v.closed).length);
                      if (node.id === 6 && k === 'layers') {
                        const types = new Set(vecs.map((v: any) => v.zone_type));
                        displayValue = String(types.size);
                      }
                      if (node.id === 5 && k === 'zones_identified') {
                        const types = new Set(vecs.map((v: any) => v.zone_type));
                        displayValue = String(types.size);
                      }
                      if (node.id === 7 && k === 'constraints') displayValue = String(csts.length);
                      if (node.id === 7 && k === 'ai_sourced') displayValue = String(csts.filter((c: any) => c.source === 'ai_extracted' || c.source === 'ai_suggested').length);
                      const progs = geo?.programmes || [];
                      const vols = geo?.volumes || [];
                      if (node.id === 8 && k === 'buildings') displayValue = String(progs.length);
                      if (node.id === 8 && k === 'uses') {
                        const allUses = new Set(progs.flatMap((p: any) => (p.uses || []).map((u: any) => u.use)));
                        displayValue = String(allUses.size);
                      }
                      if (node.id === 9 && k === 'volumes') displayValue = String(vols.length);
                      if (node.id === 9 && k === 'floors') {
                        displayValue = String(vols.filter((v: any) => v.zone_type === 'building_floor').length);
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
              {isDone && node.warning && (
                <div style={{ marginTop: '0.75rem', fontSize: '0.75rem', color: 'var(--node-warning, #f59e0b)', display: 'flex', alignItems: 'center', gap: '0.25rem' }}>
                  <AlertTriangle size={14} /> {node.warning}
                </div>
              )}
              {isDone && node.error && (
                <div style={{ marginTop: '0.75rem', fontSize: '0.75rem', color: 'var(--node-error, #ef4444)', display: 'flex', alignItems: 'center', gap: '0.25rem' }}>
                  <XCircle size={14} /> {node.error}
                </div>
              )}

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


              {/* Node 11 Manual Export */}
              {node.id === 11 && isActive && !isProcessing && (
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
