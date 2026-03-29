import React, { useState } from 'react';
import { motion } from 'framer-motion';
import { FiChevronDown, FiChevronUp, FiCopy, FiCheck } from 'react-icons/fi';
import './BlueprintDisplay.css';

function BlueprintDisplay({ blueprint }) {
  const [expandedSections, setExpandedSections] = useState({
    architecture: true,
    techstack: true,
    workflow: false,
    prerequisites: false,
    approaches: false,
    examples: false,
    references: false,
  });

  const [copied, setCopied] = useState(null);

  const toggleSection = (section) => {
    setExpandedSections((prev) => ({
      ...prev,
      [section]: !prev[section],
    }));
  };

  const handleCopy = (text, id) => {
    navigator.clipboard.writeText(text);
    setCopied(id);
    setTimeout(() => setCopied(null), 2000);
  };

  return (
    <motion.div
      className="blueprint-container"
      initial={{ opacity: 0, x: 20 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.3 }}
    >
      <div className="blueprint-header">
        <h2 className="blueprint-title">{blueprint.project_name}</h2>
        <p className="blueprint-description">{blueprint.description}</p>
      </div>

      <div className="blueprint-content">
        {/* System Architecture */}
        <CollapsibleSection
          title="System Architecture"
          id="architecture"
          expanded={expandedSections.architecture}
          toggle={toggleSection}
        >
          <div className="components-grid">
            {blueprint.system_architecture?.map((component, idx) => (
              <motion.div
                key={idx}
                className="component-card"
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: idx * 0.1 }}
              >
                <div className={`component-type ${component.type}`}>
                  {component.type.toUpperCase()}
                </div>
                <h4>{component.name}</h4>
                <p>{component.description}</p>
                <div className="component-details">
                  <div className="detail-group">
                    <strong>Responsibilities:</strong>
                    <ul>
                      {component.responsibilities?.slice(0, 2).map((resp, i) => (
                        <li key={i}>{resp}</li>
                      ))}
                    </ul>
                  </div>
                  <div className="detail-group">
                    <strong>Tech:</strong>
                    <div className="tech-tags">
                      {component.technologies?.slice(0, 3).map((tech, i) => (
                        <span key={i} className="tech-tag">
                          {tech}
                        </span>
                      ))}
                    </div>
                  </div>
                </div>
              </motion.div>
            ))}
          </div>
        </CollapsibleSection>

        {/* Tech Stack */}
        <CollapsibleSection
          title="Tech Stack"
          id="techstack"
          expanded={expandedSections.techstack}
          toggle={toggleSection}
        >
          <div className="tech-stack-grid">
            {blueprint.tech_stack?.map((item, idx) => (
              <motion.div
                key={idx}
                className="tech-item"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ delay: idx * 0.05 }}
              >
                <div className="tech-header">
                  <strong>{item.name}</strong>
                  <span className="tech-category">{item.category}</span>
                </div>
                <p className="tech-reason">{item.reason}</p>
                {item.version && <span className="tech-version">v{item.version}</span>}
              </motion.div>
            ))}
          </div>
        </CollapsibleSection>

        {/* Workflow */}
        <CollapsibleSection
          title="Workflow & Process"
          id="workflow"
          expanded={expandedSections.workflow}
          toggle={toggleSection}
        >
          <div className="workflow-timeline">
            {blueprint.workflow?.map((step, idx) => (
              <motion.div
                key={idx}
                className="workflow-step"
                initial={{ opacity: 0, x: -10 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: idx * 0.1 }}
              >
                <div className="step-number">{step.step_number}</div>
                <div className="step-content">
                  <h4>{step.title}</h4>
                  <p>{step.description}</p>
                  <div className="step-details">
                    <div className="detail">
                      <strong>Components:</strong>
                      {step.components_involved?.join(', ')}
                    </div>
                    <div className="detail">
                      <strong>Actions:</strong>
                      <ul>
                        {step.key_actions?.map((action, i) => (
                          <li key={i}>{action}</li>
                        ))}
                      </ul>
                    </div>
                  </div>
                </div>
                {idx < blueprint.workflow.length - 1 && <div className="step-arrow">↓</div>}
              </motion.div>
            ))}
          </div>
        </CollapsibleSection>

        {/* Prerequisites */}
        <CollapsibleSection
          title="Prerequisites"
          id="prerequisites"
          expanded={expandedSections.prerequisites}
          toggle={toggleSection}
        >
          <div className="prerequisites-list">
            {blueprint.prerequisites?.map((prereq, idx) => (
              <motion.div
                key={idx}
                className="prerequisite-group"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ delay: idx * 0.05 }}
              >
                <h4>{prereq.category}</h4>
                <ul>
                  {prereq.items?.map((item, i) => (
                    <li key={i}>{item}</li>
                  ))}
                </ul>
              </motion.div>
            ))}
          </div>
        </CollapsibleSection>

        {/* Solution Approaches */}
        <CollapsibleSection
          title="Solution Approaches"
          id="approaches"
          expanded={expandedSections.approaches}
          toggle={toggleSection}
        >
          <div className="approaches-grid">
            {blueprint.solution_approaches?.map((approach, idx) => (
              <motion.div
                key={idx}
                className="approach-card"
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: idx * 0.1 }}
              >
                <div className="approach-header">
                  <h4>{approach.name}</h4>
                  <span className={`complexity ${approach.complexity.toLowerCase()}`}>
                    {approach.complexity}
                  </span>
                </div>
                <p className="approach-description">{approach.description}</p>
                <div className="approach-details">
                  <div className="detail-group">
                    <strong>Pros:</strong>
                    <ul>
                      {approach.pros?.map((pro, i) => (
                        <li key={i}>✓ {pro}</li>
                      ))}
                    </ul>
                  </div>
                  <div className="detail-group">
                    <strong>Cons:</strong>
                    <ul>
                      {approach.cons?.map((con, i) => (
                        <li key={i}>✗ {con}</li>
                      ))}
                    </ul>
                  </div>
                  <div className="detail-group">
                    <strong>Timeline:</strong> {approach.estimated_time}
                  </div>
                </div>
              </motion.div>
            ))}
          </div>
        </CollapsibleSection>

        {/* Real-World Examples */}
        <CollapsibleSection
          title="Real-World Examples"
          id="examples"
          expanded={expandedSections.examples}
          toggle={toggleSection}
        >
          <div className="examples-list">
            {blueprint.real_world_examples?.map((example, idx) => (
              <motion.div
                key={idx}
                className="example-card"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ delay: idx * 0.05 }}
              >
                <h4>{example.title}</h4>
                <p className="company">Company: {example.company}</p>
                <p className="description">{example.description}</p>
                {example.link && (
                  <a href={example.link} target="_blank" rel="noopener noreferrer" className="example-link">
                    Learn more →
                  </a>
                )}
                <div className="lessons">
                  <strong>Key Lessons:</strong>
                  <ul>
                    {example.lessons_learned?.map((lesson, i) => (
                      <li key={i}>{lesson}</li>
                    ))}
                  </ul>
                </div>
              </motion.div>
            ))}
          </div>
        </CollapsibleSection>

        {/* Learning References */}
        <CollapsibleSection
          title="Learning References"
          id="references"
          expanded={expandedSections.references}
          toggle={toggleSection}
        >
          <div className="references-list">
            {blueprint.learning_references?.map((ref, idx) => (
              <motion.div
                key={idx}
                className="reference-item"
                initial={{ opacity: 0, x: -10 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: idx * 0.05 }}
              >
                <div className="reference-header">
                  <a href={ref.url} target="_blank" rel="noopener noreferrer" className="reference-link">
                    {ref.title}
                  </a>
                  <span className={`difficulty ${ref.difficulty.toLowerCase()}`}>
                    {ref.difficulty}
                  </span>
                  <span className="reference-type">{ref.type}</span>
                </div>
              </motion.div>
            ))}
          </div>
        </CollapsibleSection>

        {/* Timeline & Next Steps */}
        <motion.div
          className="blueprint-section"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
        >
          <h3 className="section-title">Timeline & Next Steps</h3>
          <div className="timeline-grid">
            {blueprint.timeline && Object.entries(blueprint.timeline).map(([phase, duration], idx) => (
              <div key={idx} className="timeline-item">
                <strong>{phase}</strong>
                <p>{duration}</p>
              </div>
            ))}
          </div>
          <div className="next-steps">
            <h4>Action Items</h4>
            <ol>
              {blueprint.next_steps?.map((step, idx) => (
                <li key={idx}>{step}</li>
              ))}
            </ol>
          </div>
        </motion.div>
      </div>
    </motion.div>
  );
}

function CollapsibleSection({ title, id, expanded, toggle, children }) {
  return (
    <div className="blueprint-section">
      <button
        className="section-header"
        onClick={() => toggle(id)}
      >
        <h3 className="section-title">{title}</h3>
        {expanded ? <FiChevronUp /> : <FiChevronDown />}
      </button>
      {expanded && (
        <motion.div
          className="section-content"
          initial={{ opacity: 0, height: 0 }}
          animate={{ opacity: 1, height: 'auto' }}
          exit={{ opacity: 0, height: 0 }}
          transition={{ duration: 0.2 }}
        >
          {children}
        </motion.div>
      )}
    </div>
  );
}

export default BlueprintDisplay;
