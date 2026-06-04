import React, { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { FiYoutube, FiBook, FiExternalLink, FiSearch } from 'react-icons/fi';
import './LearningHub.css';

/**
 * LearningHub — enhanced learning references section.
 * Shows two tabs: Documentation links (from blueprint) and YouTube search links.
 * YouTube links are generated from tech stack / project name.
 */

const DIFFICULTY_COLOR = {
  beginner: '#22c55e',
  intermediate: '#f59e0b',
  advanced: '#f87171',
};

const TYPE_ICON = {
  Tutorial: '📖',
  Documentation: '📄',
  Guide: '🗺️',
  Course: '🎓',
  Blog: '✍️',
};

function buildYouTubeLinks(blueprint) {
  const projectName = blueprint.project_name || '';
  const techs = (blueprint.tech_stack || []).map(t => t.name);
  const topics = [...new Set([projectName, ...techs])].slice(0, 12);

  return topics.map(topic => ({
    query: `${topic} tutorial for beginners`,
    label: topic,
    url: `https://www.youtube.com/results?search_query=${encodeURIComponent(topic + ' tutorial')}`,
    difficulty: getDifficulty(topic, blueprint),
    category: getCategoryForTopic(topic, blueprint),
  }));
}

function getDifficulty(topic, blueprint) {
  const approaches = blueprint.solution_approaches || [];
  const isComplex = approaches.some(a => a.complexity === 'Complex' && (a.name || '').toLowerCase().includes(topic.toLowerCase()));
  if (isComplex) return 'Advanced';
  const simpleTopics = ['html', 'css', 'git', 'github', 'react', 'express', 'node'];
  if (simpleTopics.some(t => topic.toLowerCase().includes(t))) return 'Beginner';
  return 'Intermediate';
}

function getCategoryForTopic(topic, blueprint) {
  const stack = blueprint.tech_stack || [];
  const match = stack.find(t => t.name.toLowerCase() === topic.toLowerCase());
  return match?.category || 'General';
}

export default function LearningHub({ blueprint }) {
  const [activeTab, setActiveTab] = useState('docs');
  const [docFilter, setDocFilter] = useState('All');

  const docRefs = blueprint.learning_references || [];
  const ytLinks = buildYouTubeLinks(blueprint);

  // Build unique filter types from docs
  const docTypes = ['All', ...new Set(docRefs.map(r => r.type).filter(Boolean))];

  const filteredDocs = docFilter === 'All'
    ? docRefs
    : docRefs.filter(r => r.type === docFilter);

  return (
    <div className="lhub-wrapper">
      {/* Tab bar */}
      <div className="lhub-tabs">
        <button
          className={`lhub-tab ${activeTab === 'docs' ? 'active' : ''}`}
          onClick={() => setActiveTab('docs')}
        >
          <FiBook size={14} />
          <span>Documentation & Guides</span>
          <span className="lhub-tab-count">{docRefs.length}</span>
        </button>
        <button
          className={`lhub-tab ${activeTab === 'youtube' ? 'active' : ''}`}
          onClick={() => setActiveTab('youtube')}
        >
          <FiYoutube size={14} />
          <span>YouTube Tutorials</span>
          <span className="lhub-tab-count">{ytLinks.length}</span>
        </button>
      </div>

      {/* Content */}
      <AnimatePresence mode="wait">
        {activeTab === 'docs' && (
          <motion.div
            key="docs"
            className="lhub-content"
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -6 }}
            transition={{ duration: 0.2 }}
          >
            {/* Type filter pills */}
            <div className="lhub-filter-row">
              {docTypes.map(type => (
                <button
                  key={type}
                  className={`lhub-filter-pill ${docFilter === type ? 'active' : ''}`}
                  onClick={() => setDocFilter(type)}
                >
                  {TYPE_ICON[type] || ''} {type}
                </button>
              ))}
            </div>

            <div className="lhub-docs-grid">
              {filteredDocs.length === 0 && (
                <p className="lhub-empty">No references found for this filter.</p>
              )}
              {filteredDocs.map((ref, idx) => (
                <motion.a
                  key={idx}
                  href={ref.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="lhub-doc-card"
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: idx * 0.04 }}
                  whileHover={{ y: -2 }}
                >
                  <div className="lhub-doc-top">
                    <span className="lhub-doc-type-icon">{TYPE_ICON[ref.type] || '📎'}</span>
                    <span className="lhub-doc-type">{ref.type}</span>
                    <span
                      className="lhub-doc-difficulty"
                      style={{ color: DIFFICULTY_COLOR[ref.difficulty?.toLowerCase()] || '#94a3b8' }}
                    >
                      {ref.difficulty}
                    </span>
                  </div>
                  <h4 className="lhub-doc-title">{ref.title}</h4>
                  <div className="lhub-doc-url">
                    <FiExternalLink size={11} />
                    <span>{new URL(ref.url).hostname.replace('www.', '')}</span>
                  </div>
                </motion.a>
              ))}
            </div>
          </motion.div>
        )}

        {activeTab === 'youtube' && (
          <motion.div
            key="youtube"
            className="lhub-content"
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -6 }}
            transition={{ duration: 0.2 }}
          >
            {/* <p className="lhub-yt-hint">
              <FiSearch size={12} /> Each card opens a YouTube search for that technology — pick the video that fits your level.
            </p> */}

            <div className="lhub-yt-grid">
              {ytLinks.map((item, idx) => (
                <motion.a
                  key={idx}
                  href={item.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="lhub-yt-card"
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: idx * 0.04 }}
                  whileHover={{ y: -2, scale: 1.01 }}
                >
                  <div className="lhub-yt-play">
                    <FiYoutube size={20} />
                  </div>
                  <div className="lhub-yt-info">
                    <h4>{item.label}</h4>
                    <p className="lhub-yt-category">{item.category}</p>
                    <div className="lhub-yt-meta">
                      <span
                        className="lhub-yt-difficulty"
                        style={{ color: DIFFICULTY_COLOR[item.difficulty?.toLowerCase()] || '#94a3b8' }}
                      >
                        {item.difficulty}
                      </span>
                      <span className="lhub-yt-cta">Search tutorials →</span>
                    </div>
                  </div>
                </motion.a>
              ))}
            </div>

            {/* Project-level search */}
            <div className="lhub-yt-project-search">
              <p>Looking for a complete walkthrough?</p>
              <a
                href={`https://www.youtube.com/results?search_query=${encodeURIComponent(blueprint.project_name + ' full project tutorial')}`}
                target="_blank"
                rel="noopener noreferrer"
                className="lhub-yt-full-btn"
              >
                <FiYoutube size={15} />
                Search: "{blueprint.project_name} full project tutorial"
              </a>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
