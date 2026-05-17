import React, { useState, useRef } from 'react';
import { Settings, FilePlus, Save, FolderOpen } from 'lucide-react';
import { ApiSettingsPanel } from '../settings/ApiSettingsPanel';

interface TopRibbonProps {
  onNewProject?: () => void;
  onSaveProject?: () => void;
  onLoadProject?: (data: any) => void;
}

export const TopRibbon: React.FC<TopRibbonProps> = ({ onNewProject, onSaveProject, onLoadProject }) => {
  const [showSettings, setShowSettings] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (event) => {
      try {
        const json = JSON.parse(event.target?.result as string);
        if (onLoadProject) onLoadProject(json);
      } catch (err) {
        alert("Invalid project file");
      }
    };
    reader.readAsText(file);
    // Reset input so the same file can be loaded again if needed
    e.target.value = '';
  };

  return (
    <div className="top-ribbon">
      {/* Minimal monogram — replaces old OMRT Engine label */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
        <div style={{
          width: 28, height: 28, borderRadius: 8,
          background: 'linear-gradient(135deg, var(--accent-color), #6346e0)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: '0.8rem', fontWeight: 700, color: '#fff',
          letterSpacing: '-0.02em',
          boxShadow: '0 2px 10px rgba(124,92,252,0.3)',
        }}>O</div>
      </div>
      
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
        <div style={{ display: 'flex', gap: '0.25rem', borderRight: '1px solid rgba(255,255,255,0.06)', paddingRight: '0.75rem' }}>
          <button className="btn btn-icon" onClick={onNewProject} title="New Project">
            <FilePlus size={18} />
          </button>
          <button className="btn btn-icon" onClick={() => fileInputRef.current?.click()} title="Load Project">
            <FolderOpen size={18} />
          </button>
          <button className="btn btn-icon" onClick={onSaveProject} title="Save Project">
            <Save size={18} />
          </button>
          <input 
            type="file" 
            ref={fileInputRef} 
            onChange={handleFileChange} 
            accept=".json" 
            style={{ display: 'none' }} 
          />
        </div>
        <button className="btn btn-icon" onClick={() => setShowSettings(!showSettings)} title="API Settings">
          <Settings size={18} />
        </button>
      </div>

      {showSettings && (
        <div className="settings-modal">
          <ApiSettingsPanel />
        </div>
      )}
    </div>
  );
};
