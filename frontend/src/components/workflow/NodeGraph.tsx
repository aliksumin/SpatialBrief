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
  { id: 3, title: 'Extract Metadata', desc: 'Parse text and tables', metrics: { entities: 15 } },
  { id: 4, title: 'Detect Units & Coordinates', desc: 'Detect drawing scale and origin point', metrics: { unit: 'meters', crs: 'Local' } },
  { id: 5, title: 'Separate Drawing Areas', desc: 'Analyse plot, envelopes, and no-build zones', metrics: { zones_identified: 3 } },
  { id: 6, title: 'Extract Vector Geometry', desc: 'Provide clean, colored zone graphics', metrics: { polygons: 3, layers: 3 } },
  { id: 7, title: 'Parse Annotations', desc: 'Extract layers and dimensions', metrics: { labels: 42 } },
  { id: 8, title: 'Reconstruct Geometry', desc: 'Close polylines, fix gaps', metrics: { polygons: 3, open: 0 } },
  { id: 9, title: 'Build Source Index', desc: 'Index all references', metrics: { index: 120 } },
  { id: 10, title: 'Extract Regulatory Rules', desc: 'Semantic rule parsing', metrics: { rules: 8, conf: '92%' } },
  { id: 11, title: 'Extract Programme', desc: 'GFA, uses, parking', metrics: { uses: 2, gfa: '13.5k' } },
  { id: 12, title: 'Extract Constraints', desc: 'Setbacks, heights', metrics: { constraints: 5 } },
  { id: 13, title: 'Link Rules to Geometry', desc: 'Map text to polygons', metrics: { links: 6 } },
  { id: 14, title: 'Infer Missing Variables', desc: 'Fill gaps cautiously', metrics: { inferred: 2 }, warning: 'Parking inferred from default standard' },
  { id: 15, title: 'Generate Parametric Model', desc: 'Setup Rhino/GH logics', metrics: { params: 12 } },
  { id: 16, title: 'Generate Volumes', desc: 'Spatialise into 3D', metrics: { blocks: 3 } },
  { id: 17, title: 'Detect Conflicts', desc: 'Validation checks', metrics: { conflicts: 1 }, error: 'Minor setback overlap detected' },
  { id: 18, title: 'Build UI Scene', desc: 'Prepare visualizers', metrics: { layers: 5 } },
  { id: 19, title: 'Validation Report', desc: 'Summary of process', metrics: {} },
  { id: 20, title: 'Export Package', desc: 'Rhino / Grasshopper handoff', metrics: {} }
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
      const response = await fetch('http://localhost:8000/api/v1/upload', {
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

      const response = await fetch('http://localhost:8000/api/v1/process', {
        method: 'POST',
        headers,
        body: JSON.stringify({ filenames }),
      });
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

      // Node 6: Extract Vector Geometry + Reconstruct
      if(onNodeChange) onNodeChange(6);
      await new Promise(r => setTimeout(r, 500));

      // Node 7: Parse Annotations (already done in step 3)
      if(onNodeChange) onNodeChange(7);
      await new Promise(r => setTimeout(r, 300));

      // Node 8: Reconstruct Geometry (already done in step 6)
      if(onNodeChange) onNodeChange(8);
      await new Promise(r => setTimeout(r, 300));

      // Nodes 9-20: step through remaining nodes
      for (let i = 9; i <= 20; i++) {
        if(onNodeChange) onNodeChange(i);
        await new Promise(r => setTimeout(r, 400));
      }
    } catch (error) {
      console.error('Pipeline processing failed:', error);
      alert('Processing failed. Check if the backend is running.');
    }

    setIsProcessing(false);
  };

  const handleExport = async () => {
    try {
      if ('showDirectoryPicker' in window) {
        // Prompt user to select a directory and request write access
        const dirHandle = await (window as any).showDirectoryPicker({
          mode: 'readwrite'
        });
        
        setIsProcessing(true);
        
        try {
          // 1. Create the JSON configuration file
          const jsonHandle = await dirHandle.getFileHandle('design_inputs.json', { create: true });
          const jsonWritable = await jsonHandle.createWritable();
          
          const exportData = {
            metadata: {
              project: "Sector 7 Renewal",
              generator: "OMRT Regulatory Engine",
              timestamp: new Date().toISOString()
            },
            grasshopper_parameters: {
              "Max_Height": 30,
              "Setback_Front": 5,
              "Programme_Uses": ["Residential", "Commercial"]
            },
            layers_to_import: [
              "01_SITE_AND_PARCELS", 
              "03_GENERATED_CONSTRAINTS", 
              "04_PROGRAMME_AND_MASSING_PREVIEW"
            ]
          };
          
          await jsonWritable.write(JSON.stringify(exportData, null, 2));
          await jsonWritable.close();

          // 2. Create the Rhino .3dm model file
          const rhinoHandle = await dirHandle.getFileHandle('zoning_massing.3dm', { create: true });
          const rhinoWritable = await rhinoHandle.createWritable();
          // Generating a real 3dm binary requires backend serialization. For this prototype, we output a mock binary text.
          await rhinoWritable.write("OMRT_RHINO_MOCK: In a full production build, this contains the compiled OpenNURBS binary data for layers, vectors, and breps.");
          await rhinoWritable.close();

          setTimeout(() => {
            if(onNodeChange) onNodeChange(21);
            if(onNodeChange) onNodeChange(21);
            setIsProcessing(false);
            alert(`Success! Both 'design_inputs.json' and 'zoning_massing.3dm' were saved to the '${dirHandle.name}' folder.`);
          }, 1000);
        } catch (writeErr) {
          console.error('Failed to write file:', writeErr);
          alert("Could not write file. Please ensure you granted 'Write' permissions to the folder.");
          setIsProcessing(false);
        }
      } else {
        alert("Your browser does not support native folder selection. Simulating export...");
        setIsProcessing(true);
        setTimeout(() => {
          if(onNodeChange) onNodeChange(21);
          if(onNodeChange) onNodeChange(21);
          setIsProcessing(false);
        }, 1000);
      }
    } catch (err) {
      console.log('Export cancelled or error:', err);
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
                      if (node.id === 6 && k === 'polygons') displayValue = String(vecs.filter((v: any) => v.closed).length);
                      if (node.id === 6 && k === 'layers') {
                        const types = new Set(vecs.map((v: any) => v.zone_type));
                        displayValue = String(types.size);
                      }
                      if (node.id === 5 && k === 'zones_identified') {
                        const types = new Set(vecs.map((v: any) => v.zone_type));
                        displayValue = String(types.size);
                      }
                      if (node.id === 7 && k === 'labels') displayValue = String(texts.length);
                      if (node.id === 8 && k === 'polygons') displayValue = String(vecs.filter((v: any) => v.closed).length);
                      if (node.id === 8 && k === 'open') displayValue = String(vecs.filter((v: any) => !v.closed).length);
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

              {/* Node 20 Manual Export */}
              {node.id === 20 && isActive && !isProcessing && (
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
