import React, { useEffect, useRef, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import './GenerationProgress.css';

/**
 * GenerationProgress
 * ------------------
 * Displays a Claude-style process indicator while the blueprint is generating.
 *
 * Steps are time-gated to mirror real backend phases:
 *   RAG search  ~0-6s   → 6 sources searched concurrently
 *   Fetch/embed ~6-14s  → full-text fetch + FAISS index
 *   LLM         ~14-25s → LangChain-Groq completion
 *   Parse       ~25-28s → JSON validation + blueprint assembly
 *
 * Props:
 *   visible   {boolean}  — mounts/unmounts the whole panel
 *   projectName {string} — shown in the header
 */

const STEPS = [
  {
    id:       'rag_search',
    icon:     '🔍',
    label:    'Searching research sources',
    sublabel: 'Querying arXiv, Semantic Scholar, CrossRef, CORE, Tavily, GitHub',
    duration: 5500,   // ms before auto-advancing
    color:    '#818cf8',
  },
  {
    id:       'fetch',
    icon:     '📄',
    label:    'Fetching & reading documents',
    sublabel: 'Extracting text from papers, READMEs, and documentation pages',
    duration: 5000,
    color:    '#06b6d4',
  },
  {
    id:       'embed',
    icon:     '🧠',
    label:    'Embedding & indexing knowledge',
    sublabel: 'Chunking text → SentenceTransformers → FAISS vector index',
    duration: 4500,
    color:    '#a78bfa',
  },
  {
    id:       'mmr',
    icon:     '⚡',
    label:    'Retrieving relevant context',
    sublabel: 'Running MMR retrieval — selecting diverse, high-priority chunks',
    duration: 3000,
    color:    '#f59e0b',
  },
  {
    id:       'llm',
    icon:     '✨',
    label:    'Generating blueprint',
    sublabel: 'LangChain-Groq building your architecture, tech stack & workflow',
    duration: 9000,
    color:    '#34d399',
  },
  {
    id:       'parse',
    icon:     '🔧',
    label:    'Assembling blueprint',
    sublabel: 'Validating JSON schema, merging RAG sources into references',
    duration: 3000,
    color:    '#fb923c',
  },
];

// Typing animation strings shown in the "terminal" area per step
const STEP_LOGS = {
  rag_search: [
    'GET api.semanticscholar.org/graph/v1/paper/search',
    'GET export.arxiv.org/api/query',
    'GET api.crossref.org/works',
    'GET api.core.ac.uk/v3/search/works',
    'POST api.tavily.com/search',
    'GET api.github.com/search/repositories',
  ],
  fetch: [
    'Fetching PDF streams via PyMuPDF…',
    'Parsing HTML pages via BeautifulSoup…',
    'Reading GitHub README.md files…',
    'Stripping nav/footer noise from pages…',
  ],
  embed: [
    'Loading all-MiniLM-L6-v2 encoder…',
    'RecursiveCharacterTextSplitter: chunk_size=512',
    'Encoding chunks in batches of 32…',
    'Building FAISS IndexFlatIP…',
  ],
  mmr: [
    'Query embedding normalised (cosine)',
    `Fetching top-20 candidates from FAISS…`,
    'Applying priority boost: paper > doc > repo',
    `MMR λ=0.6 → selecting 6 diverse chunks`,
  ],
  llm: [
    'Injecting research context into prompt…',
    'Sending to LangChain-Groq pipeline…',
    'Model streaming tokens…',
    'Receiving JSON blueprint…',
  ],
  parse: [
    'Stripping markdown fences…',
    'json.loads → ProjectBlueprint schema…',
    'Merging RAG source URLs into learning_references…',
    'Blueprint ready ✓',
  ],
};

function ProgressStep({ step, state }) {
  // state: 'waiting' | 'active' | 'done'
  return (
    <motion.div
      className={`gp-step gp-step--${state}`}
      initial={{ opacity: 0, x: -10 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.25 }}
    >
      <div className="gp-step-left">
        <div className="gp-step-icon-wrap" style={{ '--step-color': step.color }}>
          {state === 'active' ? (
            <span className="gp-step-spinner" style={{ borderTopColor: step.color }} />
          ) : state === 'done' ? (
            <span className="gp-step-done">✓</span>
          ) : (
            <span className="gp-step-icon">{step.icon}</span>
          )}
        </div>
        {/* connector line down */}
        <div className={`gp-step-line ${state === 'done' ? 'gp-step-line--done' : ''}`}
             style={{ background: state === 'done' ? step.color : undefined }} />
      </div>

      <div className="gp-step-body">
        <div className="gp-step-label" style={{ color: state === 'active' ? step.color : undefined }}>
          {step.label}
        </div>
        {state !== 'waiting' && (
          <motion.div
            className="gp-step-sublabel"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ delay: 0.1 }}
          >
            {step.sublabel}
          </motion.div>
        )}
      </div>
    </motion.div>
  );
}

function LogTerminal({ stepId }) {
  const [visibleLogs, setVisibleLogs] = useState([]);
  const logs = STEP_LOGS[stepId] || [];
  const intervalRef = useRef(null);

  useEffect(() => {
    setVisibleLogs([]);
    let idx = 0;

    const tick = () => {
      if (idx < logs.length) {
        const line = logs[idx];
        setVisibleLogs(prev => [...prev, line]);
        idx++;
        // vary timing slightly for realism
        intervalRef.current = setTimeout(tick, 600 + Math.random() * 400);
      }
    };

    intervalRef.current = setTimeout(tick, 200);
    return () => clearTimeout(intervalRef.current);
  }, [stepId]);

  return (
    <div className="gp-terminal">
      <div className="gp-terminal-bar">
        <span className="gp-terminal-dot" style={{ background: '#ff5f57' }} />
        <span className="gp-terminal-dot" style={{ background: '#febc2e' }} />
        <span className="gp-terminal-dot" style={{ background: '#28c840' }} />
        <span className="gp-terminal-title">process log</span>
      </div>
      <div className="gp-terminal-body">
        {visibleLogs.map((line, i) => (
          <motion.div
            key={i}
            className="gp-log-line"
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.2 }}
          >
            <span className="gp-log-prompt">›</span>
            <span>{line}</span>
          </motion.div>
        ))}
        <span className="gp-cursor">▌</span>
      </div>
    </div>
  );
}

export default function GenerationProgress({ visible, projectName }) {
  const [currentIdx, setCurrentIdx] = useState(0);
  const timerRef = useRef(null);

  // Reset and start stepping whenever we become visible
  useEffect(() => {
    if (!visible) {
      setCurrentIdx(0);
      clearTimeout(timerRef.current);
      return;
    }

    setCurrentIdx(0);

    const advance = (idx) => {
      const next = idx + 1;
      if (next < STEPS.length) {
        timerRef.current = setTimeout(() => {
          setCurrentIdx(next);
          advance(next);
        }, STEPS[idx].duration);
      }
    };

    advance(0);
    return () => clearTimeout(timerRef.current);
  }, [visible]);

  return (
    <AnimatePresence>
      {visible && (
        <motion.div
          className="gp-overlay"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.3 }}
        >
          <motion.div
            className="gp-panel"
            initial={{ opacity: 0, y: 20, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 10, scale: 0.97 }}
            transition={{ duration: 0.35, ease: 'easeOut' }}
          >
            {/* Header */}
            <div className="gp-header">
              <div className="gp-header-left">
                <div className="gp-header-pulse" />
                <span className="gp-header-title">Generating Blueprint</span>
              </div>
              {projectName && (
                <span className="gp-header-project">"{projectName}"</span>
              )}
            </div>

            {/* Steps list */}
            <div className="gp-steps">
              {STEPS.map((step, idx) => {
                const state =
                  idx < currentIdx  ? 'done'
                  : idx === currentIdx ? 'active'
                  : 'waiting';
                return (
                  <ProgressStep key={step.id} step={step} state={state} />
                );
              })}
            </div>

            {/* Live terminal for active step */}
            <LogTerminal stepId={STEPS[currentIdx]?.id} />

            {/* Footer */}
            <div className="gp-footer">
              <div className="gp-progress-bar">
                <motion.div
                  className="gp-progress-fill"
                  animate={{ width: `${((currentIdx) / STEPS.length) * 100}%` }}
                  transition={{ duration: 0.6, ease: 'easeOut' }}
                />
              </div>
              <span className="gp-footer-text">
                Step {currentIdx + 1} of {STEPS.length}
              </span>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}