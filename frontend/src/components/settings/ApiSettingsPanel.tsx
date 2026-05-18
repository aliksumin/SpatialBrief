import React, { useState } from 'react';
import { Save, Zap, CheckCircle, XCircle, Loader } from 'lucide-react';

export const ApiSettingsPanel: React.FC = () => {
  // Migrate stale model names that don't exist in the API
  const storedGoogleModel = localStorage.getItem('googleModel');
  if (storedGoogleModel && ['gemini-3.1-pro', 'gemini-3.1-flash'].includes(storedGoogleModel)) {
    localStorage.setItem('googleModel', 'gemini-2.5-flash');
  }
  const storedOpenaiModel = localStorage.getItem('openaiModel');
  if (storedOpenaiModel && ['gpt-5.5-instant'].includes(storedOpenaiModel)) {
    localStorage.setItem('openaiModel', 'gpt-5.5');
  }

  const [provider, setProvider] = useState(localStorage.getItem('apiProvider') || 'google');
  const [googleModel, setGoogleModel] = useState(localStorage.getItem('googleModel') || 'gemini-2.5-flash');
  const [openaiModel, setOpenaiModel] = useState(localStorage.getItem('openaiModel') || 'gpt-5.5');
  
  const [geminiKey, setGeminiKey] = useState(localStorage.getItem('geminiKey') || '');
  const [openaiKey, setOpenaiKey] = useState(localStorage.getItem('openaiKey') || '');

  const [testStatus, setTestStatus] = useState<'idle' | 'testing' | 'success' | 'error'>('idle');
  const [testMessage, setTestMessage] = useState('');

  const handleSave = () => {
    localStorage.setItem('apiProvider', provider);
    localStorage.setItem('googleModel', googleModel);
    localStorage.setItem('openaiModel', openaiModel);
    localStorage.setItem('geminiKey', geminiKey.trim());
    localStorage.setItem('openaiKey', openaiKey.trim());
    alert('API Settings saved securely to local storage.');
  };

  const handleTestKey = async () => {
    const key = geminiKey.trim();
    if (!key) {
      setTestStatus('error');
      setTestMessage('No API key entered.');
      return;
    }

    setTestStatus('testing');
    setTestMessage('Validating key with Gemini API...');

    try {
      // Use Gemini's list-models endpoint as a lightweight validation
      const resp = await fetch(
        `https://generativelanguage.googleapis.com/v1beta/models?key=${encodeURIComponent(key)}`
      );
      if (resp.ok) {
        const data = await resp.json();
        const modelCount = data.models?.length || 0;
        setTestStatus('success');
        setTestMessage(`Key is valid! ${modelCount} models available.`);
      } else {
        const errData = await resp.json().catch(() => ({}));
        const errMsg = errData?.error?.message || `HTTP ${resp.status}`;
        setTestStatus('error');
        setTestMessage(`Invalid key: ${errMsg}`);
      }
    } catch (e: any) {
      setTestStatus('error');
      setTestMessage(`Connection error: ${e.message}`);
    }
  };

  const googleModels = [
    { value: 'gemini-2.5-flash', label: 'Gemini 2.5 Flash (Recommended)' },
    { value: 'gemini-2.5-pro', label: 'Gemini 2.5 Pro' },
    { value: 'gemini-3.1-pro-preview', label: 'Gemini 3.1 Pro (Preview)' },
    { value: 'gemini-3.1-flash-lite', label: 'Gemini 3.1 Flash Lite' },
  ];

  const openaiModels = [
    { value: 'gpt-5.5', label: 'GPT-5.5 (Recommended)' },
    { value: 'gpt-5.5-pro', label: 'GPT-5.5 Pro' },
    { value: 'gpt-5.4', label: 'GPT-5.4' },
    { value: 'gpt-5.4-mini', label: 'GPT-5.4 Mini' },
  ];

  const selectStyle = {
    width: '100%',
    background: 'rgba(0, 0, 0, 0.2)',
    border: '1px solid var(--border-color)',
    color: 'var(--text-primary)',
    padding: '0.75rem',
    borderRadius: '8px',
    outline: 'none',
    appearance: 'none' as const,
  };

  return (
    <div>
      <h2 style={{ fontSize: '1.25rem', marginBottom: '0.5rem' }}>API Settings</h2>
      <p style={{ color: 'var(--text-secondary)', marginBottom: '1.5rem', fontSize: '0.875rem' }}>
        Configure your AI models to enable rule extraction and semantic mapping.
      </p>
      
      <div className="input-group">
        <label>AI Provider</label>
        <select 
          value={provider} 
          onChange={(e) => setProvider(e.target.value)}
          style={selectStyle}
        >
          <option value="google">Google</option>
          <option value="openai">OpenAI</option>
        </select>
      </div>

      {provider === 'google' && (
        <>
          <div className="input-group">
            <label>Model</label>
            <select 
              value={googleModel} 
              onChange={(e) => setGoogleModel(e.target.value)}
              style={selectStyle}
            >
              {googleModels.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
            </select>
          </div>
          <div className="input-group">
            <label>Gemini API Key</label>
            <input 
              type="password" 
              value={geminiKey} 
              onChange={(e) => { setGeminiKey(e.target.value); setTestStatus('idle'); }} 
              placeholder="AIzaSy..."
            />
          </div>
          {/* Test Key Button */}
          <button 
            className="btn" 
            onClick={handleTestKey}
            disabled={testStatus === 'testing'}
            style={{ 
              marginTop: '0.5rem', 
              width: '100%',
              background: testStatus === 'success' ? 'rgba(34, 197, 94, 0.15)' 
                : testStatus === 'error' ? 'rgba(239, 68, 68, 0.15)' 
                : 'rgba(255, 255, 255, 0.05)',
              border: `1px solid ${
                testStatus === 'success' ? '#22c55e' 
                : testStatus === 'error' ? '#ef4444' 
                : 'var(--border-color)'
              }`,
              color: testStatus === 'success' ? '#22c55e' 
                : testStatus === 'error' ? '#ef4444' 
                : 'var(--text-primary)',
            }}
          >
            {testStatus === 'testing' ? <Loader size={16} className="animate-spin" /> 
              : testStatus === 'success' ? <CheckCircle size={16} />
              : testStatus === 'error' ? <XCircle size={16} />
              : <Zap size={16} />}
            {' '}
            {testStatus === 'testing' ? 'Testing...' : 'Test API Key'}
          </button>
          {testMessage && (
            <div style={{ 
              marginTop: '0.5rem', 
              fontSize: '0.8rem', 
              color: testStatus === 'success' ? '#22c55e' : testStatus === 'error' ? '#ef4444' : 'var(--text-secondary)',
              padding: '0.5rem 0.75rem',
              background: testStatus === 'success' ? 'rgba(34, 197, 94, 0.08)' : testStatus === 'error' ? 'rgba(239, 68, 68, 0.08)' : 'transparent',
              borderRadius: '6px',
            }}>
              {testMessage}
            </div>
          )}
        </>
      )}

      {provider === 'openai' && (
        <>
          <div className="input-group">
            <label>Model</label>
            <select 
              value={openaiModel} 
              onChange={(e) => setOpenaiModel(e.target.value)}
              style={selectStyle}
            >
              {openaiModels.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
            </select>
          </div>
          <div className="input-group">
            <label>OpenAI API Key</label>
            <input 
              type="password" 
              value={openaiKey} 
              onChange={(e) => setOpenaiKey(e.target.value)} 
              placeholder="sk-..."
            />
          </div>
        </>
      )}
      
      <button className="btn" onClick={handleSave} style={{ marginTop: '1.5rem' }}>
        <Save size={18} /> Save Settings
      </button>
    </div>
  );
};
