import React, { useState } from 'react';
import { Save } from 'lucide-react';

export const ExtractionSettingsPanel: React.FC = () => {
  const [plotColor, setPlotColor] = useState(localStorage.getItem('omrt_plotColor') || '#3b82f6');
  const [plotLinetype, setPlotLinetype] = useState(localStorage.getItem('omrt_plotLinetype') || 'solid');

  const [buildableColor, setBuildableColor] = useState(localStorage.getItem('omrt_buildableColor') || '#ef4444');
  const [buildableLinetype, setBuildableLinetype] = useState(localStorage.getItem('omrt_buildableLinetype') || 'dashed');

  const [noBuildColor, setNoBuildColor] = useState(localStorage.getItem('omrt_noBuildColor') || '#f59e0b');
  const [noBuildLinetype, setNoBuildLinetype] = useState(localStorage.getItem('omrt_noBuildLinetype') || 'dotted');

  const handleSave = () => {
    localStorage.setItem('omrt_plotColor', plotColor);
    localStorage.setItem('omrt_plotLinetype', plotLinetype);
    localStorage.setItem('omrt_buildableColor', buildableColor);
    localStorage.setItem('omrt_buildableLinetype', buildableLinetype);
    localStorage.setItem('omrt_noBuildColor', noBuildColor);
    localStorage.setItem('omrt_noBuildLinetype', noBuildLinetype);
    
    window.dispatchEvent(new Event('omrt_graphics_settings_updated'));
    alert('Extraction Graphic Settings saved.');
  };

  const selectStyle = {
    width: '100%',
    background: 'rgba(0, 0, 0, 0.2)',
    border: '1px solid var(--border-color)',
    color: 'var(--text-primary)',
    padding: '0.75rem',
    borderRadius: '8px',
    outline: 'none',
    appearance: 'none' as const,
    marginTop: '0.5rem'
  };

  const inputStyle = {
    width: '100%',
    padding: '0.5rem',
    background: 'rgba(0,0,0,0.2)',
    border: '1px solid var(--border-color)',
    borderRadius: '8px',
    color: '#fff',
    marginTop: '0.5rem'
  };

  const Group = ({ title, color, setColor, linetype, setLinetype }: any) => (
    <div style={{ marginBottom: '1.5rem', padding: '1rem', background: 'rgba(255,255,255,0.02)', borderRadius: '8px', border: '1px solid rgba(255,255,255,0.05)' }}>
      <h3 style={{ fontSize: '1rem', marginBottom: '1rem', borderBottom: '1px solid rgba(255,255,255,0.1)', paddingBottom: '0.5rem' }}>{title}</h3>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>
        <div>
          <label style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>Color</label>
          <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', marginTop: '0.5rem' }}>
            <input type="color" value={color} onChange={e => setColor(e.target.value)} style={{ width: '40px', height: '40px', padding: 0, border: 'none', borderRadius: '4px', cursor: 'pointer', background: 'transparent' }} />
            <input type="text" value={color} onChange={e => setColor(e.target.value)} style={{ ...inputStyle, marginTop: 0 }} />
          </div>
        </div>
        <div>
          <label style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>Linetype</label>
          <select value={linetype} onChange={e => setLinetype(e.target.value)} style={{ ...selectStyle, marginTop: '0.5rem' }}>
            <option value="solid">Solid</option>
            <option value="dashed">Dashed</option>
            <option value="dotted">Dotted</option>
          </select>
        </div>
      </div>
    </div>
  );

  return (
    <div style={{ marginTop: '2rem' }}>
      <h2 style={{ fontSize: '1.25rem', marginBottom: '0.5rem' }}>Graphic Extraction Settings</h2>
      <p style={{ color: 'var(--text-secondary)', marginBottom: '1.5rem', fontSize: '0.875rem' }}>
        Configure the display colors and linetypes for extracted vector geometries.
      </p>

      <Group title="Plot Boundary" color={plotColor} setColor={setPlotColor} linetype={plotLinetype} setLinetype={setPlotLinetype} />
      <Group title="Buildable Envelope" color={buildableColor} setColor={setBuildableColor} linetype={buildableLinetype} setLinetype={setBuildableLinetype} />
      <Group title="No-Build Zone" color={noBuildColor} setColor={setNoBuildColor} linetype={noBuildLinetype} setLinetype={setNoBuildLinetype} />

      <button className="btn" onClick={handleSave} style={{ marginTop: '1rem' }}>
        <Save size={18} /> Save Graphic Settings
      </button>
    </div>
  );
};
