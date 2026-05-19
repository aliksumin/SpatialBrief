import React, { useState, useEffect } from 'react';
import { TopRibbon } from './components/layout/TopRibbon';
import { NodeGraph } from './components/workflow/NodeGraph';
import { ThreeSceneManager } from './components/viewer/ThreeSceneManager';
import { ErrorBoundary } from './ErrorBoundary';
import './index.css';

export default function App() {
  const [uploadedBundle, setUploadedBundle] = useState<any>(() => {
    const saved = localStorage.getItem('omrt_bundle');
    try {
      return saved ? JSON.parse(saved) : null;
    } catch (e) {
      console.error("Failed to parse omrt_bundle from localStorage", e);
      return null;
    }
  });
  
  const MAX_NODE = 11;
  
  const [activeNode, setActiveNode] = useState<number>(() => {
    const saved = localStorage.getItem('omrt_activeNode');
    const val = saved ? parseInt(saved, 10) : 1;
    return val > MAX_NODE ? MAX_NODE : val;
  });
  
  const [selectedNode, setSelectedNode] = useState<number | null>(() => {
    const saved = localStorage.getItem('omrt_selectedNode');
    if (!saved || saved === 'null') return null;
    const val = parseInt(saved, 10);
    return val > MAX_NODE ? MAX_NODE : val;
  });
  const [selectedVector, setSelectedVector] = useState<any>(null);
  const [activeTab, setActiveTab] = useState<string>('Data');

  // Auto-save to localStorage
  useEffect(() => {
    localStorage.setItem('omrt_bundle', JSON.stringify(uploadedBundle));
  }, [uploadedBundle]);

  useEffect(() => {
    localStorage.setItem('omrt_activeNode', activeNode.toString());
  }, [activeNode]);

  useEffect(() => {
    localStorage.setItem('omrt_selectedNode', selectedNode !== null ? selectedNode.toString() : 'null');
  }, [selectedNode]);

  const handleNodeChange = (n: number) => {
    setActiveNode(n);
    setSelectedNode(n); // auto-follow during execution
  };

  // --- Project Handlers ---
  const handleNewProject = () => {
    localStorage.removeItem('omrt_bundle');
    localStorage.removeItem('omrt_activeNode');
    localStorage.removeItem('omrt_selectedNode');
    localStorage.removeItem('omrt_visibleLayers');
    setUploadedBundle(null);
    setActiveNode(1);
    setSelectedNode(null);
    setSelectedVector(null);
    window.dispatchEvent(new Event('omrt-project-loaded'));
  };

  const handleSaveProject = async () => {
    const projectState = {
      uploadedBundle,
      activeNode,
      selectedNode,
      visibleLayers: (() => {
        try {
          return JSON.parse(localStorage.getItem('omrt_visibleLayers') || '{}');
        } catch (e) {
          return {};
        }
      })()
    };
    const jsonString = JSON.stringify(projectState, null, 2);
    
    try {
      if ('showSaveFilePicker' in window) {
        const handle = await (window as any).showSaveFilePicker({
          suggestedName: 'omrt_project.json',
          types: [{
            description: 'JSON Files',
            accept: { 'application/json': ['.json'] },
          }],
        });
        const writable = await handle.createWritable();
        await writable.write(jsonString);
        await writable.close();
      } else {
        // Fallback for browsers without File System Access API
        const blob = new Blob([jsonString], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'omrt_project.json';
        a.click();
        URL.revokeObjectURL(url);
      }
    } catch (err) {
      console.log('Save cancelled or failed', err);
    }
  };

  const handleLoadProject = (projectData: any) => {
    if (projectData.uploadedBundle !== undefined) setUploadedBundle(projectData.uploadedBundle);
    if (projectData.activeNode !== undefined) setActiveNode(projectData.activeNode);
    if (projectData.selectedNode !== undefined) setSelectedNode(projectData.selectedNode);
    if (projectData.visibleLayers) {
      localStorage.setItem('omrt_visibleLayers', JSON.stringify(projectData.visibleLayers));
      window.dispatchEvent(new Event('omrt-project-loaded'));
    }
  };

  const getExtractedData = () => {
    return uploadedBundle?.geometry || null;
  };

  const renderDataView = () => {
    if (selectedNode === null) return (
      <div style={{ color: 'var(--text-secondary)', padding: '2rem', textAlign: 'center' }}>
        <h3>Context Mode Active</h3>
        <p>Select a node from the pipeline to inspect specific extracted data.</p>
      </div>
    );

    // Node 1: show uploaded files
    if (selectedNode === 1) {
      const fnames = uploadedBundle?.filenames || uploadedBundle?.geometry?.source_files || [];
      if (fnames.length === 0) return <div style={{ color: 'var(--text-secondary)' }}>No files uploaded yet.</div>;
      return (
        <div className="data-grid">
          {fnames.map((f: string, i: number) => (
            <div key={i} className="data-card">
              <div className="data-card-header">Uploaded File</div>
              <div className="data-card-value" style={{ fontSize: '0.95rem' }}>{f}</div>
              <span className="data-tag">{f.toLowerCase().endsWith('.pdf') ? 'Document' : 'CAD Vector'}</span>
            </div>
          ))}
        </div>
      );
    }

    if (!uploadedBundle) return <div style={{ color: 'var(--text-secondary)' }}>Awaiting documents...</div>;
    const data = getExtractedData();
    if (!data) return <div style={{ color: 'var(--text-secondary)' }}>Processing data...</div>;

    return (
      <div className="data-grid" style={{ gap: '2rem' }}>
        {/* Node 2: Document Classification */}
        {selectedNode === 2 && (
          <>
            {data.source_files?.map((file: string, index: number) => {
              const isCad = file.toLowerCase().endsWith('.dwg') || file.toLowerCase().endsWith('.dxf');
              const role = isCad ? "Zoning / Land Use" : "Regulatory Document";
              return (
                <div key={index} className="data-card">
                  <div className="data-card-header">Input Document</div>
                  <div className="data-card-value">{file}</div>
                  <div><span className="data-tag">{role}</span></div>
                </div>
              );
            })}
          </>
        )}

        {/* Node 3: Extract Metadata */}
        {selectedNode === 3 && (
          <div className="data-card" style={{ gridColumn: '1 / -1' }}>
            <div className="data-card-header" style={{ color: 'var(--accent-color)' }}>
              <span className="data-tag" style={{ margin: 0, marginRight: '0.5rem' }}>METADATA</span>
              Extracted Text &amp; Annotations
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem', marginTop: '1rem' }}>
              <div>
                <div className="data-card-value" style={{ fontSize: '2rem', color: '#3b82f6' }}>{data.source_files?.length || 0}</div>
                <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>Source Documents</div>
              </div>
              <div>
                <div className="data-card-value" style={{ fontSize: '2rem', color: '#22c55e' }}>{data.extracted_text?.length || 0}</div>
                <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>Text Blocks Extracted</div>
              </div>
            </div>
            {data.extracted_text && data.extracted_text.length > 0 && (
              <div style={{ marginTop: '1.5rem', borderTop: '1px solid rgba(255,255,255,0.1)', paddingTop: '1rem', maxHeight: '200px', overflowY: 'auto' }}>
                <h4 style={{ fontSize: '0.75rem', textTransform: 'uppercase', color: 'var(--text-secondary)', marginBottom: '0.75rem', letterSpacing: '0.05em' }}>Parsed Text Blocks</h4>
                {data.extracted_text.map((item: any, i: number) => (
                  <div key={i} style={{ background: 'rgba(0,0,0,0.2)', padding: '0.6rem 0.8rem', borderRadius: '6px', marginBottom: '0.4rem', fontSize: '0.8rem' }}>
                    <div style={{ color: 'var(--text-primary)', whiteSpace: 'pre-wrap' }}>{item.text}</div>
                    <div style={{ fontSize: '0.7rem', color: 'var(--text-secondary)', marginTop: '0.25rem' }}>
                      Source: {item.source} {item.layer ? `· Layer: ${item.layer}` : ''}
                    </div>
                  </div>
                ))}
              </div>
            )}
            {(!data.extracted_text || data.extracted_text.length === 0) && (
              <div style={{ marginTop: '1rem', color: 'var(--text-secondary)', fontSize: '0.85rem' }}>No text metadata extracted from the uploaded documents.</div>
            )}
          </div>
        )}

        {/* Node 4: Units & Coordinates */}
        {selectedNode === 4 && (
          <div className="data-card" style={{ gridColumn: '1 / -1' }}>
            <div className="data-card-header" style={{ color: 'var(--accent-color)' }}>
              Coordinate System Analysis
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1.5rem', marginTop: '1rem' }}>
              <div>
                <div className="data-card-value">Meters (m)</div>
                <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>Detected Base Unit</div>
              </div>
              <div>
                <div className="data-card-value">[0.0, 0.0, 0.0]</div>
                <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>Normalized Local Origin</div>
              </div>
            </div>
          </div>
        )}

        {/* Node 5: Drawing Areas Separation */}
        {selectedNode === 5 && (
          <div className="data-card" style={{ gridColumn: '1 / -1' }}>
            <div className="data-card-header" style={{ color: 'var(--accent-color)' }}>
              Drawing Areas Identification
            </div>
            <div style={{ marginTop: '1rem' }}>
              <div className="data-card-value">Essential Zones Analyzed</div>
              <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginBottom: '1rem' }}>Ready for extraction</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem' }}>
                <span className="data-tag" style={{ background: 'rgba(59, 130, 246, 0.15)', borderColor: '#3b82f6', color: '#60a5fa' }}>Plot Boundary</span>
                <span className="data-tag" style={{ background: 'rgba(239, 68, 68, 0.15)', borderColor: '#ef4444', color: '#f87171' }}>Buildable Envelope</span>
                <span className="data-tag" style={{ background: 'rgba(245, 158, 11, 0.15)', borderColor: '#f59e0b', color: '#fbbf24' }}>No-Build Zone</span>
              </div>
            </div>
          </div>
        )}

        {/* Node 6: Vector Geometry */}
        {selectedNode === 6 && (
          <div className="data-card" style={{ gridColumn: '1 / -1' }}>
            <div className="data-card-header" style={{ color: 'var(--accent-color)' }}>
              <span className="data-tag" style={{ margin: 0, marginRight: '0.5rem' }}>PIPELINE</span> 
              Clean Vector Geometry Extraction
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '1.5rem', marginTop: '1rem' }}>
              <div>
                <div className="data-card-value" style={{ fontSize: '2rem', color: '#22c55e' }}>{data.extracted_objects}</div>
                <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>Clean Zones Extracted</div>
              </div>
              <div>
                <div className="data-card-value" style={{ fontSize: '2rem', color: '#3b82f6' }}>
                  {data.raw_vector_objects?.filter((o: any) => o.closed).length || 0}
                </div>
                <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>Closed Polygons</div>
              </div>
              <div>
                <div className="data-card-value" style={{ fontSize: '2rem', color: '#f59e0b' }}>
                  {data.raw_vector_objects ? Math.round(
                    data.raw_vector_objects.reduce((sum: number, o: any) => sum + (o.confidence || 0), 0) / 
                    Math.max(data.raw_vector_objects.length, 1) * 100
                  ) : 0}%
                </div>
                <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>Avg Confidence</div>
              </div>
            </div>

            {/* Per-zone-type breakdown */}
            <div style={{ marginTop: '1.5rem', borderTop: '1px solid rgba(255,255,255,0.1)', paddingTop: '1rem' }}>
              <h4 style={{ fontSize: '0.75rem', textTransform: 'uppercase', color: 'var(--text-secondary)', marginBottom: '0.75rem', letterSpacing: '0.05em' }}>Zone Classification Breakdown</h4>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: '0.5rem' }}>
                {(() => {
                  const zoneColors: Record<string, string> = {
                    plot_boundary: '#3b82f6', buildable_envelope: '#f97316',
                    infrastructure_zone: '#94a3b8', landscape_zone: '#22c55e',
                    restriction_line: '#ef4444', zone_boundary: '#f59e0b',
                    parcel_line: '#06b6d4', sub_zone: '#8b5cf6',
                    major_boundary: '#e2e8f0', filled_zone: '#fb923c',
                    uncategorized_zone: '#f97316', traffic_zone: '#64748b',
                    no_build_zone: '#ef4444',
                  };
                  const counts: Record<string, number> = {};
                  data.raw_vector_objects?.forEach((o: any) => {
                    const zt = o.zone_type || 'unknown';
                    counts[zt] = (counts[zt] || 0) + 1;
                  });
                  return Object.entries(counts).sort((a, b) => b[1] - a[1]).map(([zt, count]) => (
                    <div key={zt} style={{
                      display: 'flex', alignItems: 'center', gap: '0.5rem',
                      background: 'rgba(255,255,255,0.03)', padding: '0.5rem 0.75rem',
                      borderRadius: '6px', border: `1px solid ${zoneColors[zt] || '#666'}30`,
                    }}>
                      <div style={{ width: 8, height: 8, borderRadius: '50%', background: zoneColors[zt] || '#666', flexShrink: 0 }} />
                      <span style={{ fontSize: '0.8rem', color: 'var(--text-primary)' }}>{zt.replace(/_/g, ' ')}</span>
                      <span style={{ marginLeft: 'auto', fontWeight: 700, color: zoneColors[zt] || '#999', fontSize: '0.85rem' }}>{count}</span>
                    </div>
                  ));
                })()}
              </div>
            </div>

            {/* Zone details table */}
            {data.raw_vector_objects?.length > 0 && data.raw_vector_objects.length <= 60 && (
              <div style={{ marginTop: '1rem', maxHeight: '200px', overflowY: 'auto' }}>
                <table style={{ width: '100%', fontSize: '0.75rem', borderCollapse: 'collapse' }}>
                  <thead>
                    <tr style={{ color: 'var(--text-secondary)', textAlign: 'left', borderBottom: '1px solid rgba(255,255,255,0.1)' }}>
                      <th style={{ padding: '0.4rem 0.5rem' }}>ID</th>
                      <th style={{ padding: '0.4rem 0.5rem' }}>Type</th>
                      <th style={{ padding: '0.4rem 0.5rem' }}>Zone</th>
                      <th style={{ padding: '0.4rem 0.5rem' }}>Label</th>
                      <th style={{ padding: '0.4rem 0.5rem' }}>Confidence</th>
                      <th style={{ padding: '0.4rem 0.5rem' }}>Method</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.raw_vector_objects.map((v: any, i: number) => (
                      <tr key={i} style={{ borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
                        <td style={{ padding: '0.3rem 0.5rem', fontFamily: 'monospace', color: 'var(--text-secondary)' }}>{v.id?.slice(0, 12)}</td>
                        <td style={{ padding: '0.3rem 0.5rem' }}>{v.type}</td>
                        <td style={{ padding: '0.3rem 0.5rem' }}>
                          <span style={{ color: v.color_hint || '#999' }}>{v.zone_type?.replace(/_/g, ' ')}</span>
                        </td>
                        <td style={{ padding: '0.3rem 0.5rem', color: v.zone_label ? 'var(--text-primary)' : 'var(--text-secondary)', fontSize: '0.7rem' }}>{v.zone_label || '—'}</td>
                        <td style={{ padding: '0.3rem 0.5rem' }}>
                          <span style={{ color: v.confidence >= 0.8 ? '#22c55e' : v.confidence >= 0.6 ? '#f59e0b' : '#ef4444' }}>
                            {Math.round((v.confidence || 0) * 100)}%
                          </span>
                        </td>
                        <td style={{ padding: '0.3rem 0.5rem', color: 'var(--text-secondary)' }}>{v.classification_method}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {/* Node 7: Extract Constraints */}
        {selectedNode === 7 && (
          <div className="data-card" style={{ gridColumn: '1 / -1' }}>
            <div className="data-card-header" style={{ color: 'var(--accent-color)' }}>
              <span className="data-tag" style={{ margin: 0, marginRight: '0.5rem' }}>CONSTRAINTS</span>
              Regulatory Constraints
            </div>
            {(() => {
              const constraints = data.constraints || [];
              const summary = data.constraint_summary || {};
              if (constraints.length === 0) {
                return (
                  <div style={{ marginTop: '1rem', color: 'var(--text-secondary)', fontSize: '0.85rem' }}>
                    No constraints extracted. Upload regulatory documents with constraint data.
                  </div>
                );
              }
              const catColors: Record<string, string> = {
                height: '#3b82f6', setback: '#22c55e', density: '#f59e0b',
                parking: '#f97316', programme: '#8b5cf6', environmental: '#06b6d4',
                facade: '#ec4899', access: '#64748b', other: '#94a3b8',
              };
              return (
                <>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', gap: '1rem', marginTop: '1rem' }}>
                    <div>
                      <div className="data-card-value" style={{ fontSize: '2rem', color: '#22c55e' }}>{summary.total || constraints.length}</div>
                      <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>Total Constraints</div>
                    </div>
                    <div>
                      <div className="data-card-value" style={{ fontSize: '2rem', color: '#3b82f6' }}>{summary.regex_extracted || 0}</div>
                      <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>Pattern Extracted</div>
                    </div>
                    <div>
                      <div className="data-card-value" style={{ fontSize: '2rem', color: '#8b5cf6' }}>{summary.ai_extracted || 0}</div>
                      <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>AI Extracted</div>
                    </div>
                    <div>
                      <div className="data-card-value" style={{ fontSize: '2rem', color: '#f59e0b' }}>{summary.ai_suggested || 0}</div>
                      <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>AI Suggested</div>
                    </div>
                  </div>
                  <div style={{ marginTop: '1.5rem', borderTop: '1px solid rgba(255,255,255,0.1)', paddingTop: '1rem', maxHeight: '280px', overflowY: 'auto' }}>
                    <h4 style={{ fontSize: '0.75rem', textTransform: 'uppercase', color: 'var(--text-secondary)', marginBottom: '0.75rem', letterSpacing: '0.05em' }}>Constraint Details</h4>
                    <div style={{ display: 'grid', gap: '0.5rem' }}>
                      {constraints.map((c: any, i: number) => (
                        <div key={i} style={{
                          display: 'flex', alignItems: 'center', gap: '0.75rem',
                          background: 'rgba(0,0,0,0.2)', padding: '0.7rem 1rem',
                          borderRadius: '8px', borderLeft: `3px solid ${catColors[c.category] || '#666'}`,
                        }}>
                          <div style={{ flex: 1 }}>
                            <div style={{ fontSize: '0.85rem', color: 'var(--text-primary)', fontWeight: 600, display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                              {c.name}
                              {c.source === 'ai_suggested' && (
                                <span style={{ fontSize: '0.6rem', background: 'rgba(245,158,11,0.2)', color: '#fbbf24', padding: '0.1rem 0.4rem', borderRadius: '4px', border: '1px solid rgba(245,158,11,0.3)' }}>AI Suggested</span>
                              )}
                              {c.source === 'ai_extracted' && (
                                <span style={{ fontSize: '0.6rem', background: 'rgba(139,92,246,0.2)', color: '#a78bfa', padding: '0.1rem 0.4rem', borderRadius: '4px', border: '1px solid rgba(139,92,246,0.3)' }}>AI Extracted</span>
                              )}
                            </div>
                            {c.raw_quote && (
                              <div style={{ fontSize: '0.7rem', color: 'var(--text-secondary)', marginTop: '0.25rem', fontStyle: 'italic' }}>"{c.raw_quote}"</div>
                            )}
                          </div>
                          <div style={{ textAlign: 'right', flexShrink: 0 }}>
                            <div style={{ fontSize: '1.1rem', fontWeight: 700, color: catColors[c.category] || '#999' }}>{c.value} <span style={{ fontSize: '0.75rem', fontWeight: 400 }}>{c.unit}</span></div>
                            <div style={{ fontSize: '0.65rem', color: 'var(--text-secondary)' }}>
                              <span style={{ color: (c.confidence || 0) >= 0.7 ? '#22c55e' : '#f59e0b' }}>{Math.round((c.confidence || 0) * 100)}%</span> · {c.category}
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                </>
              );
            })()}
          </div>
        )}

        {/* Node 8: Extract Programme (Using Extracted Text) */}
        {selectedNode === 8 && (
          <div className="data-card" style={{ gridColumn: '1 / -1' }}>
            <div className="data-card-header" style={{ color: 'var(--accent-color)' }}>
              <span className="data-tag" style={{ margin: 0, marginRight: '0.5rem' }}>INPUT: Document Text</span> 
              Extracted Rules & Metadata
            </div>
            <div style={{ marginTop: '0.5rem', marginBottom: '1rem' }}>
              <span style={{ color: 'var(--text-primary)' }}>Raw Text Extracted from Files</span>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr', gap: '1rem', marginTop: '1rem', maxHeight: '400px', overflowY: 'auto' }}>
              {data.extracted_text && data.extracted_text.length > 0 ? (
                data.extracted_text.map((item: any, i: number) => (
                  <div key={i} style={{ background: 'rgba(0,0,0,0.2)', padding: '1rem', borderRadius: '8px' }}>
                    <div className="data-card-value" style={{ fontSize: '1rem', whiteSpace: 'pre-wrap' }}>{item.text}</div>
                    <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginTop: '0.5rem' }}>Source: {item.source} {item.layer ? `(Layer: ${item.layer})` : ''}</div>
                  </div>
                ))
              ) : (
                <div style={{ color: 'var(--text-secondary)' }}>No text extracted from the documents.</div>
              )}
            </div>
          </div>
        )}
        
        {/* Node 9+: Volumes, Validation & Export */}
        {selectedNode >= 9 && (
          <div className="data-card" style={{ gridColumn: '1 / -1' }}>
             <div className="data-card-header" style={{ color: 'var(--accent-color)' }}>Spatial Generation & Validation</div>
             <div style={{ marginTop: '1rem', color: 'var(--text-secondary)' }}>Waiting for full 3D generation algorithms based on extracted vectors...</div>
          </div>
        )}
      </div>
    );
  };

  const getSourceRef = () => {
    if (selectedNode === null) return 'Select a node to see source references.';
    const files = uploadedBundle?.geometry?.source_files || uploadedBundle?.filenames || [];
    const fileList = files.join(', ') || 'None';
    const srcMap: Record<number, string> = {
      1: `Input bundle: ${fileList}`,
      2: `Classifying: ${fileList}`,
      3: `Text extraction & annotations from: ${fileList}`,
      4: `Coordinate detection from CAD headers`,
      5: `Spatial analysis of closed polygons from vector data`,
      6: `Vector geometry extraction & reconstruction from: ${fileList}`,
      7: `Constraint extraction (regex + AI) from document text`,
      8: `Programme data (GFA, uses) from regulatory documents`,
      9: `3D volumes generated from footprints + programme + constraints`,
      10: `Validation report across all pipeline stages`,
      11: `Export package: Rhino/Grasshopper handoff`,
    };
    return srcMap[selectedNode] || `Source: ${fileList}`;
  };

  const getConflicts = () => {
    if (selectedNode === null) return 'Select a node to see potential issues.';
    const conflictMap: Record<number, string> = {
      1: 'No issues — files loaded successfully.',
      2: 'No classification conflicts detected.',
      3: 'No metadata or annotation extraction issues.',
      4: 'Verify that detected units match the drawing intent.',
      5: 'Check that all zones are properly separated.',
      6: 'Review zone classification and geometry reconstruction accuracy in the viewport.',
      7: 'Review extracted constraint values against source documents.',
      8: 'Confirm GFA and use allocations are correct.',
      9: 'Check generated volumes against site constraints.',
      10: 'Review full validation report before export.',
      11: 'Verify export package completeness.',
    };
    return conflictMap[selectedNode] || 'No issues detected for this node.';
  };

  const getClassificationMode = () => {
    return uploadedBundle?.geometry?.classification_mode || uploadedBundle?.classification_mode || null;
  };

  const renderStatusIndicator = () => {
    const mode = getClassificationMode();
    if (!mode || mode === 'no_files' || mode === 'no_pages') {
      return null;
    }

    const configs: Record<string, { className: string; dotColor: string; label: string; message: string }> = {
      'ai_vision': {
        className: 'ai-vision',
        dotColor: '#34d399',
        label: 'AI Vision',
        message: 'Zone classification powered by Gemini Vision',
      },
      'ai_ensemble': {
        className: 'ai-vision',
        dotColor: '#34d399',
        label: 'AI Ensemble',
        message: 'Multi-agent classification powered by Gemini',
      },
      'rule_based': {
        className: 'rule-based',
        dotColor: '#fbbf24',
        label: 'Rule-Based',
        message: 'Classification using geometric heuristics',
      },
      'rule_based_no_key': {
        className: 'no-key',
        dotColor: '#94a3b8',
        label: 'Rule-Based',
        message: 'No Gemini API key configured — using rule-based classification. Add your key in Settings for AI-powered accuracy.',
      },
      'rule_based_fallback': {
        className: 'error',
        dotColor: '#f87171',
        label: 'Rule-Based (Fallback)',
        message: (() => {
          const detail = uploadedBundle?.ai_error_detail || uploadedBundle?.geometry?.ai_error_detail;
          if (detail) return `AI Vision failed: ${detail}`;
          return 'AI Vision classification failed — fell back to rule-based classification. Check your API key or try again.';
        })(),
      },
    };

    const config = configs[mode] || configs['rule_based'];

    return (
      <>
        <span className={`status-indicator ${config.className}`}>
          <span className="status-dot pulse" style={{ background: config.dotColor }} />
          {config.label}
        </span>
        <span style={{ marginLeft: '0.5rem', opacity: 0.7 }}>{config.message}</span>
      </>
    );
  };

  return (
    <div className="app-container">
      <TopRibbon 
        onNewProject={handleNewProject} 
        onSaveProject={handleSaveProject} 
        onLoadProject={handleLoadProject} 
      />
      <div className="content-area">
        <div className="sidebar-nodes">
          <NodeGraph 
            activeNode={activeNode}
            fileCount={uploadedBundle?.filenames?.length}
            extractionData={uploadedBundle}
            onFilesUploaded={setUploadedBundle} 
            onNodeChange={handleNodeChange} 
            selectedNode={selectedNode}
            onSelectNode={setSelectedNode}
          />
        </div>
        <div className="main-content" onClick={() => setSelectedNode(null)}>
          <div className="viewport-container">
            <ErrorBoundary>
              <ThreeSceneManager selectedNode={selectedNode} activeNode={activeNode} onSelectNode={setSelectedNode} onSelectVector={setSelectedVector} projectData={uploadedBundle} />
            </ErrorBoundary>
          </div>
          <div className="bottom-panel" onClick={(e) => e.stopPropagation()}>
            <div className="panel-header">
              <h3>{selectedVector ? 'Selected Object' : `Node ${selectedNode} Analysis Data`}</h3>
              <div className="tabs">
                {(selectedVector ? ['Details', 'Source Ref'] : ['Data', 'Source Ref', 'Conflicts']).map(tab => (
                  <button 
                    key={tab} 
                    className={`tab ${activeTab === tab ? 'active' : ''}`}
                    onClick={() => setActiveTab(tab)}
                  >
                    {tab}
                  </button>
                ))}
              </div>
            </div>
            <div className="panel-content" style={{ padding: '1.5rem', whiteSpace: activeTab === 'Data' || activeTab === 'Details' ? 'normal' : 'pre-wrap' }}>
              {/* Vector detail view */}
              {selectedVector && activeTab === 'Details' && (
                <div className="vector-detail-grid">
                  <div className="vector-detail-item">
                    <span className="vector-detail-label">ID</span>
                    <span className="vector-detail-value mono">{selectedVector.id}</span>
                  </div>
                  <div className="vector-detail-item">
                    <span className="vector-detail-label">Zone Type</span>
                    <span className="vector-detail-value" style={{ color: selectedVector.color_hint || '#94a3b8' }}>{selectedVector.zone_type?.replace(/_/g, ' ')}</span>
                  </div>
                  <div className="vector-detail-item">
                    <span className="vector-detail-label">Geometry</span>
                    <span className="vector-detail-value">{selectedVector.type} · {selectedVector.closed ? 'Closed' : 'Open'}</span>
                  </div>
                  {selectedVector.zone_label && (
                    <div className="vector-detail-item">
                      <span className="vector-detail-label">Label</span>
                      <span className="vector-detail-value" style={{ color: '#60a5fa' }}>{selectedVector.zone_label}</span>
                    </div>
                  )}
                  <div className="vector-detail-item">
                    <span className="vector-detail-label">Points</span>
                    <span className="vector-detail-value">{selectedVector.points?.length || 0}</span>
                  </div>
                  <div className="vector-detail-item">
                    <span className="vector-detail-label">Confidence</span>
                    <span className="vector-detail-value" style={{ color: (selectedVector.confidence || 0) >= 0.8 ? '#22c55e' : '#f59e0b' }}>{Math.round((selectedVector.confidence || 0) * 100)}%</span>
                  </div>
                  <div className="vector-detail-item">
                    <span className="vector-detail-label">Method</span>
                    <span className="vector-detail-value mono">{selectedVector.classification_method}</span>
                  </div>
                  {selectedVector.source_layer && (
                    <div className="vector-detail-item">
                      <span className="vector-detail-label">Source Layer</span>
                      <span className="vector-detail-value">{selectedVector.source_layer}</span>
                    </div>
                  )}
                  {selectedVector.area_pdf_units > 0 && (
                    <div className="vector-detail-item">
                      <span className="vector-detail-label">Area (PDF units)</span>
                      <span className="vector-detail-value">{selectedVector.area_pdf_units}</span>
                    </div>
                  )}
                </div>
              )}
              {/* Normal views */}
              {!selectedVector && activeTab === 'Data' && renderDataView()}
              {activeTab === 'Source Ref' && getSourceRef()}
              {!selectedVector && activeTab === 'Conflicts' && getConflicts()}
            </div>
          </div>
        </div>
      </div>
      {/* Status Bar */}
      <div className="status-bar">
        <div className="status-bar-section">
          {uploadedBundle ? renderStatusIndicator() : (
            <span style={{ opacity: 0.5 }}>Ready — Upload documents to begin analysis</span>
          )}
        </div>
        <div className="status-bar-section" style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          {(() => {
            const aiModels = uploadedBundle?.geometry?.ai_models || uploadedBundle?.ai_models || {};
            const taskLabels: Record<string, { label: string; color: string }> = {
              vision_classification: { label: 'Vision', color: '#34d399' },
              constraint_extraction: { label: 'Constraints', color: '#a78bfa' },
              zone_validation: { label: 'Validation', color: '#60a5fa' },
            };
            const entries = Object.entries(aiModels);
            if (entries.length > 0) {
              return entries.map(([task, model]) => {
                const cfg = taskLabels[task] || { label: task, color: '#94a3b8' };
                return (
                  <span key={task} style={{
                    display: 'inline-flex', alignItems: 'center', gap: '0.35rem',
                    fontSize: '0.7rem', padding: '0.15rem 0.5rem',
                    borderRadius: '4px', 
                    background: `${cfg.color}12`, 
                    border: `1px solid ${cfg.color}30`,
                    color: cfg.color,
                  }}>
                    <span style={{ width: 5, height: 5, borderRadius: '50%', background: cfg.color, flexShrink: 0 }} />
                    {cfg.label}: <span style={{ color: 'var(--text-primary)', fontWeight: 600 }}>{String(model)}</span>
                  </span>
                );
              });
            }
            // No AI models used — show configured model or engine label
            const configuredModel = localStorage.getItem('googleModel');
            return (
              <span style={{ opacity: 0.5, fontSize: '0.75rem' }}>
                {uploadedBundle?.geometry?.extracted_objects 
                  ? `${uploadedBundle.geometry.extracted_objects} objects extracted`
                  : configuredModel ? `Model: ${configuredModel}` : 'OMRT Engine'}
              </span>
            );
          })()}
        </div>
      </div>
    </div>
  );
}
