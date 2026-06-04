import React, { useState, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { FiMonitor, FiSmartphone, FiTablet, FiRefreshCw, FiDownload, FiMaximize2, FiX } from 'react-icons/fi';
import { toPng } from 'html-to-image';
import { apiClient } from '../services/api';
import './ProjectPreview.css';

const VIEWPORTS = [
  { id: 'desktop', icon: FiMonitor, label: 'Desktop', width: '100%' },
  { id: 'tablet', icon: FiTablet, label: 'Tablet', width: '768px' },
  { id: 'mobile', icon: FiSmartphone, label: 'Mobile', width: '390px' },
];

export default function ProjectPreview({ blueprint }) {
  const [html, setHtml] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [viewport, setViewport] = useState('desktop');
  const [fullscreen, setFullscreen] = useState(false);
  const [generated, setGenerated] = useState(false);
  const iframeRef = useRef(null);
  const previewRef = useRef(null);

  const generate = async () => {
    setLoading(true);
    setError('');
    setHtml('');

    try {
      // Build a rich context string from the blueprint
      const techNames = (blueprint.tech_stack || []).map(t => t.name).join(', ');
      const archTypes = (blueprint.system_architecture || [])
        .map(a => `${a.type}: ${a.name}`)
        .join(', ');
      const context = [
        blueprint.description,
        techNames ? `Tech stack: ${techNames}` : '',
        archTypes ? `Architecture: ${archTypes}` : '',
      ].filter(Boolean).join('. ');

      const response = await apiClient.post('/api/preview', {
        problem_statement: blueprint.project_name,
        context,
      });

      const raw = response.data?.html || '';
      if (!raw || !raw.includes('<')) {
        throw new Error('Preview returned invalid HTML. Try regenerating.');
      }

      setHtml(raw);
      setGenerated(true);
    } catch (err) {
      const msg = err.response?.data?.detail || err.message || 'Failed to generate preview.';
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  const handleDownload = async () => {
    if (!previewRef.current) return;
    try {
      const dataUrl = await toPng(previewRef.current, { cacheBust: true });
      const link = document.createElement('a');
      link.href = dataUrl;
      link.download = `${(blueprint.project_name || 'preview').replace(/\s+/g, '-').toLowerCase()}-ui.png`;
      document.body.appendChild(link);
      link.click();
      link.remove();
    } catch (err) {
      console.error('Download failed', err);
    }
  };

  const currentViewport = VIEWPORTS.find(v => v.id === viewport);

  return (
    <div className="preview-wrapper">
      {/* Top toolbar */}
      <div className="preview-toolbar">
        <div className="preview-viewport-switcher">
          {VIEWPORTS.map(v => (
            <button
              key={v.id}
              className={`preview-vp-btn ${viewport === v.id ? 'active' : ''}`}
              onClick={() => setViewport(v.id)}
              title={v.label}
            >
              <v.icon size={15} />
              <span>{v.label}</span>
            </button>
          ))}
        </div>

        <div className="preview-actions">
          {generated && (
            <>
              <button className="preview-action-btn" onClick={generate} title="Regenerate">
                <FiRefreshCw size={14} />
                <span>Regenerate</span>
              </button>
              <button className="preview-action-btn" onClick={handleDownload} title="Download PNG">
                <FiDownload size={14} />
                <span>Download</span>
              </button>
              <button className="preview-action-btn" onClick={() => setFullscreen(true)} title="Fullscreen">
                <FiMaximize2 size={14} />
              </button>
            </>
          )}
        </div>
      </div>

      {/* Preview area */}
      <div className="preview-stage" ref={previewRef}>
        {!generated && !loading && (
          <motion.div
            className="preview-placeholder"
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
          >
            <div className="preview-placeholder-icon">
              <FiMonitor size={32} />
            </div>
            <h4>UI Preview</h4>
            <p>
              Generate a live UI mockup of what your{' '}
              <strong>{blueprint.project_name}</strong> app could look like.
            </p>
            <button className="preview-generate-btn" onClick={generate}>
              Generate Preview
            </button>
          </motion.div>
        )}

        {loading && (
          <motion.div
            className="preview-loading"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
          >
            <div className="preview-loading-ring" />
            <p>Designing your UI…</p>
            <span>This takes ~15 seconds</span>
          </motion.div>
        )}

        {error && !loading && (
          <div className="preview-error">
            <p>{error}</p>
            <button onClick={generate}>Try again</button>
          </div>
        )}

        {html && !loading && (
          <motion.div
            className="preview-browser-chrome"
            initial={{ opacity: 0, scale: 0.98 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ duration: 0.4 }}
          >
            {/* Browser chrome top bar */}
            <div className="browser-bar">
              <div className="browser-dots">
                <span className="dot red" />
                <span className="dot yellow" />
                <span className="dot green" />
              </div>
              <div className="browser-url">
                <span className="browser-lock">🔒</span>
                <span>
                  {blueprint.project_name?.toLowerCase().replace(/\s+/g, '-')}.app
                </span>
              </div>
            </div>

            {/* Responsive iframe wrapper */}
            <div
              className="browser-viewport"
              style={{ maxWidth: currentViewport.width }}
            >
              <iframe
                ref={iframeRef}
                srcDoc={html}
                sandbox="allow-scripts allow-same-origin"
                title={`${blueprint.project_name} UI Preview`}
                className="preview-iframe"
              />
            </div>
          </motion.div>
        )}
      </div>

      {/* Fullscreen modal */}
      <AnimatePresence>
        {fullscreen && html && (
          <motion.div
            className="preview-fullscreen-overlay"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
          >
            <div className="preview-fullscreen-topbar">
              <span>{blueprint.project_name} — UI Preview</span>
              <button
                onClick={() => setFullscreen(false)}
                className="preview-fullscreen-close"
              >
                <FiX size={18} />
              </button>
            </div>
            <iframe
              srcDoc={html}
              sandbox="allow-scripts allow-same-origin"
              title="Fullscreen Preview"
              className="preview-fullscreen-iframe"
            />
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}