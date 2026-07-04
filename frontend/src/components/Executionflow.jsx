import React, { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { apiClient } from '../services/api';
import './ExecutionFlow.css';

const LANES = [
  { id: 'user',     label: 'User',          icon: '👤', color: '#818cf8', bg: 'rgba(129,140,248,0.06)' },
  { id: 'frontend', label: 'Frontend',      icon: '🖥️', color: '#06b6d4', bg: 'rgba(6,182,212,0.06)'   },
  { id: 'backend',  label: 'Backend / API', icon: '⚙️', color: '#f59e0b', bg: 'rgba(245,158,11,0.06)'  },
  { id: 'ai',       label: 'AI / LLM',      icon: '🤖', color: '#a78bfa', bg: 'rgba(167,139,250,0.06)' },
  { id: 'database', label: 'Database',      icon: '🗄️', color: '#34d399', bg: 'rgba(52,211,153,0.06)'  },
  { id: 'output',   label: 'Output',        icon: '✅', color: '#fb923c', bg: 'rgba(251,146,60,0.06)'  },
];

const LANE_MAP = {
  user:     LANES[0],
  frontend: LANES[1],
  backend:  LANES[2],
  ai:       LANES[3],
  database: LANES[4],
  output:   LANES[5],
};

/* ─── shape types for nodes ─── */
// start/end → rounded pill   decision → diamond   process → rectangle
function NodeShape({ type = 'process', color, children }) {
  const cls = `eflow-shape eflow-shape-${type}`;
  return (
    <div className={cls} style={{ borderColor: color, '--node-color': color }}>
      {children}
    </div>
  );
}

/* ─── Arrow between two lanes ─── */
function CrossArrow({ fromLane, toLane, visibleLanes, color, label }) {
  const fromIdx = visibleLanes.findIndex(l => l.id === fromLane);
  const toIdx   = visibleLanes.findIndex(l => l.id === toLane);
  if (fromIdx === -1 || toIdx === -1 || fromIdx === toIdx) return null;

  const direction = toIdx > fromIdx ? 'right' : 'left';
  const span      = Math.abs(toIdx - fromIdx);

  return (
    <div
      className={`eflow-cross-arrow eflow-cross-arrow--${direction}`}
      style={{
        '--arrow-color': color,
        '--arrow-span':  span,
        gridColumn: direction === 'right'
          ? `${fromIdx + 2} / ${toIdx + 3}`  // +2 for badge col
          : `${toIdx + 2}   / ${fromIdx + 3}`,
      }}
    >
      {label && <span className="eflow-cross-label">{label}</span>}
    </div>
  );
}

export default function ExecutionFlow({ blueprint, flowchartRef, onDownload }) {
  const [steps,      setSteps]      = useState(null);   // [{lane, type, title, detail, arrowTo, arrowLabel}]
  const [loading,    setLoading]    = useState(false);
  const [error,      setError]      = useState('');
  const [activeStep, setActiveStep] = useState(null);

  const generate = async () => {
    setLoading(true);
    setError('');
    setSteps(null);
    setActiveStep(null);
    try {
      const res = await apiClient.post('/api/runtime-flow', {
        problem_statement: blueprint.project_name,
        context: [
          blueprint.description,
          (blueprint.tech_stack || []).map(t => t.name).join(', '),
          (blueprint.system_architecture || []).map(a => `${a.type}:${a.name}`).join(', '),
        ].filter(Boolean).join(' | '),
      });
      const data = res.data?.steps;
      if (!Array.isArray(data) || !data.length) throw new Error('No steps returned.');
      setSteps(data);
    } catch (e) {
      setError(e.response?.data?.detail || e.message || 'Failed to generate flow.');
    } finally {
      setLoading(false);
    }
  };

  /* which lanes actually appear in the steps */
  const activeLaneIds  = steps ? new Set(steps.map(s => s.lane)) : new Set();
  const visibleLanes   = LANES.filter(l => activeLaneIds.has(l.id));

  return (
    <div className="eflow-wrapper">

      {/* ── Legend + controls ── */}
      <div className="eflow-header">
        {steps ? (
          <div className="eflow-legend">
            {visibleLanes.map(lane => (
              <span key={lane.id} className="eflow-legend-item">
                <span className="eflow-legend-dot" style={{ background: lane.color }} />
                {/* <span>{lane.icon}</span>  */}
                {lane.label}
              </span>
            ))}
          </div>
        ) : <div />}
        <div className="eflow-header-actions">
          {/* {steps && (
            <button className="eflow-action-btn" onClick={generate} title="Regenerate">↺ Regenerate</button>
          )} */}
          {steps && (
            <button className="eflow-action-btn" onClick={onDownload}>Download PNG</button>
          )}
        </div>
      </div>

      {/* ── Empty / loading / error states ── */}
      {!steps && !loading && !error && (
        <motion.div
          className="eflow-placeholder"
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
        >
          <div className="eflow-placeholder-icon">⚡</div>
          <h4>Runtime Execution Flow</h4>
          <p>
            See exactly how <strong>{blueprint.project_name}</strong> runs at runtime —
            from user action through every layer to final output.
          </p>
          <button className="eflow-generate-btn" onClick={generate}>Generate Flow</button>
        </motion.div>
      )}

      {loading && (
        <motion.div className="eflow-loading" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
          <div className="eflow-loading-ring" />
          <p>Mapping runtime flow…</p>
          <span>Analysing architecture layers</span>
        </motion.div>
      )}

      {error && !loading && (
        <div className="eflow-error">
          <p>{error}</p>
          <button onClick={generate}>Try again</button>
        </div>
      )}

      {/* ── Swimlane diagram ── */}
      {steps && !loading && (
        <motion.div
          className="eflow-diagram"
          ref={flowchartRef}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.35 }}
        >
          {/* column grid: badge + one col per visible lane */}
          <div
            className="eflow-grid"
            style={{ gridTemplateColumns: `48px repeat(${visibleLanes.length}, 1fr)` }}
          >
            {/* ── Lane header row ── */}
            <div className="eflow-grid-badge-header" />
            {visibleLanes.map(lane => (
              <div
                key={lane.id}
                className="eflow-lane-header"
                style={{ borderBottomColor: lane.color, color: lane.color, background: lane.bg }}
              >
                {/* <span className="eflow-lane-icon">{lane.icon}</span> */}
                {lane.label}
              </div>
            ))}

            {/* ── Step rows ── */}
            {steps.map((step, idx) => {
              const lane     = LANE_MAP[step.lane] || LANES[1];
              const isActive = activeStep === idx;
              const laneCol  = visibleLanes.findIndex(l => l.id === step.lane) + 2; // +2 for badge col

              return (
                <React.Fragment key={idx}>
                  {/* Badge col */}
                  <div className="eflow-badge-cell">
                    <div className="eflow-badge" style={{ color: lane.color, borderColor: `${lane.color}60` }}>
                      {String(idx + 1).padStart(2, '0')}
                    </div>
                    {idx < steps.length - 1 && (
                      <div className="eflow-badge-line" style={{ background: `${lane.color}40` }} />
                    )}
                  </div>

                  {/* One cell per lane */}
                  {visibleLanes.map((l, li) => {
                    const isPrimary = l.id === step.lane;
                    return (
                      <div
                        key={l.id}
                        className={`eflow-cell ${isPrimary ? 'eflow-cell--primary' : ''}`}
                        style={{ background: l.bg }}
                      >
                        {isPrimary && (
                          <motion.div
                            className={`eflow-node eflow-node--${step.type || 'process'} ${isActive ? 'eflow-node--active' : ''}`}
                            style={{ '--node-color': l.color }}
                            onClick={() => setActiveStep(isActive ? null : idx)}
                            initial={{ opacity: 0, scale: 0.92 }}
                            animate={{ opacity: 1, scale: 1 }}
                            transition={{ delay: idx * 0.05 }}
                            whileHover={{ scale: 1.03 }}
                            whileTap={{ scale: 0.97 }}
                          >
                            {/* shape wrapper */}
                            <div className={`eflow-shape eflow-shape--${step.type || 'process'}`}>
                              <span className="eflow-node-title">{step.title}</span>
                            </div>
                            {/* expand hint */}
                            <div className="eflow-node-hint">
                              {isActive ? '▲' : '▼'} {isActive ? 'collapse' : 'expand'}
                            </div>
                          </motion.div>
                        )}

                        {/* cross-lane arrow indicator dot */}
                        {!isPrimary && step.arrowTo && step.arrowTo === l.id && (
                          <div className="eflow-arrow-target" style={{ '--node-color': l.color }}>
                            <span>Next Step</span>
                          </div>
                        )}
                      </div>
                    );
                  })}

                  {/* ── Detail panel (full-width row) ── */}
                  <AnimatePresence>
                    {isActive && (
                      <>
                        {/* spacer badge col */}
                        <div className="eflow-detail-badge-spacer" />
                        <motion.div
                          className="eflow-detail-panel"
                          style={{
                            borderLeftColor: lane.color,
                            gridColumn: `2 / ${visibleLanes.length + 2}`,
                          }}
                          initial={{ opacity: 0, height: 0 }}
                          animate={{ opacity: 1, height: 'auto' }}
                          exit={{ opacity: 0, height: 0 }}
                          transition={{ duration: 0.2 }}
                        >
                          <div className="eflow-detail-lane-tag" style={{ background: lane.bg, color: lane.color, borderColor: `${lane.color}40` }}>
                            {/* {lane.icon}  */}
                            {lane.label}
                          </div>
                          <p className="eflow-detail-desc">{step.detail}</p>
                          {step.arrowTo && (
                            <div className="eflow-detail-arrow-info">
                              <span style={{ color: lane.color }}>↓ passes to</span>
                              <span className="eflow-detail-arrow-target" style={{ color: LANE_MAP[step.arrowTo]?.color }}>
                                {/* {LANE_MAP[step.arrowTo]?.icon}  */}
                                {LANE_MAP[step.arrowTo]?.label}
                              </span>
                              {step.arrowLabel && <span className="eflow-detail-arrow-label">"{step.arrowLabel}"</span>}
                            </div>
                          )}
                        </motion.div>
                      </>
                    )}
                  </AnimatePresence>

                  {/* ── Connector arrow between steps (in badge col only) ── */}
                  {idx < steps.length - 1 && !isActive && (
                    <>
                      <div className="eflow-connector-cell">
                        <div className="eflow-connector-line" style={{ background: `${lane.color}50` }} />
                        <div className="eflow-connector-arrow" style={{ borderTopColor: LANE_MAP[steps[idx + 1].lane]?.color || lane.color }} />
                      </div>
                      {visibleLanes.map(l => (
                        <div key={l.id} className="eflow-connector-spacer" style={{ background: l.bg }} />
                      ))}
                    </>
                  )}
                </React.Fragment>
              );
            })}
          </div>
        </motion.div>
      )}
    </div>
  );
}