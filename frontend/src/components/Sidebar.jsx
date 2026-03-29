import React from 'react';
import './Sidebar.css';
import { motion } from 'framer-motion';
import { FiX, FiMessageSquare, FiSettings, FiHelpCircle, FiGithub } from 'react-icons/fi';

function Sidebar({ isOpen, onClose }) {
  const examples = [
    'Build an E-commerce Platform',
    'Real-time Chat Application',
    'Fitness Tracking App',
    'AI Content Generator',
    'Healthcare Management System',
  ];

  return (
    <>
      {isOpen && (
        <motion.div
          className="sidebar-overlay"
          onClick={onClose}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
        />
      )}
      <motion.aside
        className={`sidebar ${isOpen ? 'open' : ''}`}
        initial={{ x: '-100%' }}
        animate={{ x: isOpen ? '0%' : '-100%' }}
        transition={{ duration: 0.3 }}
      >
        <div className="sidebar-header">
          <h2>AI Architect</h2>
          <button onClick={onClose} className="close-button">
            <FiX size={24} />
          </button>
        </div>

        <div className="sidebar-content">
          <div className="sidebar-section">
            <h3>
              <FiMessageSquare size={18} />
              Quick Examples
            </h3>
            <div className="examples-list">
              {examples.map((example, idx) => (
                <button key={idx} className="example-button">
                  {example}
                </button>
              ))}
            </div>
          </div>

          <div className="sidebar-section">
            <h3>
              <FiHelpCircle size={18} />
              Tips
            </h3>
            <div className="tips-list">
              <p>✓ Be specific about your requirements</p>
              <p>✓ Mention any constraints you have</p>
              <p>✓ Include your tech preferences</p>
              <p>✓ Specify your timeline if possible</p>
            </div>
          </div>

          <div className="sidebar-section">
            <h3>
              <FiSettings size={18} />
              Settings
            </h3>
            <label className="setting-item">
              <input type="checkbox" defaultChecked />
              <span>Dark Mode</span>
            </label>
            <label className="setting-item">
              <input type="checkbox" defaultChecked />
              <span>Auto-save</span>
            </label>
          </div>
        </div>

        <div className="sidebar-footer">
          <button className="footer-button">
            <FiGithub size={20} />
            GitHub
          </button>
          <p className="version">v1.0.0</p>
        </div>
      </motion.aside>
    </>
  );
}

export default Sidebar;
