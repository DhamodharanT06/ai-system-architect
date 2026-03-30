import React, { useEffect, useRef, useState } from 'react';
import { motion } from 'framer-motion';
import { FiChevronDown, FiChevronUp, FiX } from 'react-icons/fi';
import './BlueprintDisplay.css';
import { toPng } from 'html-to-image';

function BlueprintDisplay({ blueprint, isFocusMode = false }) {
  const [expandedSections, setExpandedSections] = useState({
    architecture: true,
    techstack: true,
    workflow: false,
    prerequisites: false,
    approaches: false,
    examples: false,
    references: false,
  });

  const toggleSection = (section) => {
    setExpandedSections((prev) => ({
      ...prev,
      [section]: !prev[section],
    }));
  };

  const architectureNodes = (blueprint.system_architecture || []).map((item) => ({
    label: item.name,
    kind: item.type || 'architecture',
    detailTitle: item.name,
    detailType: item.type || 'architecture',
    detailText: item.description || 'No description available.',
    bullets: [
      ...(item.responsibilities || []).slice(0, 4),
      ...((item.technologies || []).slice(0, 3).map((tech) => `Tech: ${tech}`)),
    ],
  }));

  const topicNodes = [
    ...(blueprint.tech_stack || []).map((item) => ({
      label: item.name,
      kind: 'tech',
      detailTitle: item.name,
      detailType: item.category || 'technology',
      detailText: item.reason || 'Technology selected for this project.',
      bullets: [item.version ? `Version: ${item.version}` : 'Version: latest stable'],
    })),
    ...(blueprint.prerequisites || []).map((item) => ({
      label: item.category,
      kind: 'prerequisite',
      detailTitle: item.category,
      detailType: 'prerequisite',
      detailText: 'Requirements to build this project successfully.',
      bullets: (item.items || []).slice(0, 5),
    })),
    ...(blueprint.solution_approaches || []).map((item) => ({
      label: item.name,
      kind: 'approach',
      detailTitle: item.name,
      detailType: item.complexity || 'approach',
      detailText: item.description || 'Recommended implementation approach.',
      bullets: [
        ...(item.pros || []).slice(0, 2).map((pro) => `Pro: ${pro}`),
        ...(item.cons || []).slice(0, 2).map((con) => `Watch: ${con}`),
      ],
    })),
  ]
    .filter(Boolean)
    .filter((value, index, self) => self.findIndex((v) => v.label === value.label) === index);

  const stackLanguages = [...new Set((blueprint.tech_stack || []).flatMap((item) => item.languages || []))];
  const stackFrameworks = [...new Set((blueprint.tech_stack || []).flatMap((item) => item.frameworks || []))];
  const stackModules = [...new Set((blueprint.tech_stack || []).flatMap((item) => item.modules || []))];

  const mindmapNodes = [...architectureNodes, ...topicNodes].slice(0, 12);
  const orbitX = mindmapNodes.length > 9 ? 38 : 33;
  const orbitY = mindmapNodes.length > 9 ? 30 : 26;
  const workflowFlow = blueprint.workflow || [];
  const [selectedNode, setSelectedNode] = useState(null);
  const detailPanelRef = useRef(null);
  const mindmapRef = useRef(null);
  const flowchartRef = useRef(null);
  const architectureRef = useRef(null);
  const techstackRef = useRef(null);
  const workflowRef = useRef(null);
  const prerequisitesRef = useRef(null);
  const approachesRef = useRef(null);
  const examplesRef = useRef(null);
  const referencesRef = useRef(null);

  const architectureTechMap = new Map(
    (blueprint.system_architecture || []).map((component) => [
      component.name,
      component.technologies || [],
    ])
  );

  const getStepStacks = (step) => {
    const stacks = [];
    (step.components_involved || []).forEach((componentName) => {
      const techs = architectureTechMap.get(componentName) || [];
      techs.forEach((tech) => {
        if (!stacks.includes(tech)) {
          stacks.push(tech);
        }
      });
    });
    return stacks.slice(0, 5);
  };

  useEffect(() => {
    if (!selectedNode || !detailPanelRef.current) return;
    detailPanelRef.current.scrollTop = 0;
    detailPanelRef.current.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, [selectedNode]);

  const handleDownload = async (ref, name) => {
    if (!ref || !ref.current) return;
    try {
      const dataUrl = await toPng(ref.current, { cacheBust: true });
      const link = document.createElement('a');
      link.href = dataUrl;
      link.download = `${name}.png`;
      document.body.appendChild(link);
      link.click();
      link.remove();
    } catch (err) {
      console.error('Failed to generate image', err);
    }
  };

  return (
    <motion.div
      className={`blueprint-container ${isFocusMode ? 'focus-grid-mode' : ''}`}
      initial={{ opacity: 0, x: 20 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.3 }}
    >
      <div className="blueprint-header">
        <h2 className="blueprint-title">{blueprint.project_name}</h2>
        <p className="blueprint-description">{blueprint.description}</p>
      </div>

      <div className="blueprint-content">
        <motion.div
          className="blueprint-section"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
        >
          <div className="section-header-row">
            <h3 className="section-title visual-title mindmap-title">Project Mindmap</h3>
            <button className="section-download" onClick={() => handleDownload(mindmapRef, 'mindmap')}>Download PNG</button>
          </div>
          <div className="mindmap-shell">
            <div ref={mindmapRef} className={`mindmap-layout ${selectedNode ? 'has-detail' : 'no-detail'}`}>
              <div className="mindmap-canvas">
              <svg className="mindmap-links" viewBox="0 0 1000 700" preserveAspectRatio="none">
                {mindmapNodes.map((node, idx) => {
                  const angle = (Math.PI * 2 * idx) / Math.max(mindmapNodes.length, 1);
                  const x = 50 + Math.cos(angle) * orbitX;
                  const y = 50 + Math.sin(angle) * orbitY;
                  return (
                    <line
                      key={`line-${node.label}-${idx}`}
                      x1="50%"
                      y1="50%"
                      x2={`${x}%`}
                      y2={`${y}%`}
                      className="mindmap-line"
                    />
                  );
                })}
              </svg>

              <div className="mindmap-center">
                <span>Core Idea</span>
                <strong>{blueprint.project_name}</strong>
              </div>

              {mindmapNodes.map((node, idx) => {
                const angle = (Math.PI * 2 * idx) / Math.max(mindmapNodes.length, 1);
                const x = 50 + Math.cos(angle) * orbitX;
                const y = 50 + Math.sin(angle) * orbitY;
                return (
                  <div
                    key={`node-${node.label}-${idx}`}
                    className={`mindmap-node ${node.kind}`}
                    style={{ left: `${x}%`, top: `${y}%` }}
                    role="button"
                    tabIndex={0}
                    onClick={() => setSelectedNode(node)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        setSelectedNode(node);
                      }
                    }}
                  >
                    {node.label}
                  </div>
                );
              })}
              </div>

              {selectedNode && (
                <aside className="mindmap-detail-panel" ref={detailPanelRef}>
                  <div className="detail-panel-head">
                    <p className="detail-kicker">Node Details</p>
                    <button
                      className="detail-close"
                      onClick={() => setSelectedNode(null)}
                      title="Close details"
                    >
                      <FiX size={14} />
                    </button>
                  </div>
                  <h4>{selectedNode.detailTitle}</h4>
                  <div className="detail-badge">{selectedNode.detailType}</div>
                  <p>{selectedNode.detailText}</p>
                  {selectedNode.bullets?.length > 0 && (
                    <ul>
                      {selectedNode.bullets.map((item, idx) => (
                        <li key={`${item}-${idx}`}>{item}</li>
                      ))}
                    </ul>
                  )}
                </aside>
              )}
            </div>
          </div>
        </motion.div>

        <motion.div
          className="blueprint-section"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
        >
          <div className="section-header-row">
            <h3 className="section-title visual-title">Flowchart</h3>
            <button className="section-download" onClick={() => handleDownload(flowchartRef, 'flowchart')}>Download PNG</button>
          </div>
          <div className="flowchart-shell">
            <div ref={flowchartRef} className="flowchart-track">
              {workflowFlow.map((step, idx) => (
                <div className="flowchart-item" key={`${step.step_number}-${step.title}-${idx}`}>
                  <div className="flowchart-node">
                    <div className="flowchart-step">Phase {step.step_number}</div>
                    <h4>{step.title}</h4>
                    <p>{step.description}</p>
                    <div className="flowchart-section-block">
                      <strong>Process Topic</strong>
                      <div className="flowchart-chip-row">
                        {(step.components_involved || []).slice(0, 4).map((item, chipIdx) => (
                          <span key={`${item}-${chipIdx}`} className="flowchart-chip">{item}</span>
                        ))}
                        {(!step.components_involved || step.components_involved.length === 0) && (
                          <span className="flowchart-chip muted">General flow</span>
                        )}
                      </div>
                    </div>
                    <div className="flowchart-section-block">
                      <strong>Stack Involved</strong>
                      <div className="flowchart-chip-row">
                        {getStepStacks(step).map((stackItem, stackIdx) => (
                          <span key={`${stackItem}-${stackIdx}`} className="flowchart-chip stack">{stackItem}</span>
                        ))}
                        {getStepStacks(step).length === 0 && (
                          <span className="flowchart-chip muted">Derived during implementation</span>
                        )}
                      </div>
                    </div>
                    <div className="flowchart-meta">
                      <strong>Simple Phase Goal:</strong> Complete this step to move the system safely to the next stage.
                    </div>
                  </div>
                  {idx < workflowFlow.length - 1 && <div className="flowchart-arrow">→</div>}
                </div>
              ))}
            </div>
          </div>
        </motion.div>

        {/* System Architecture */}
        <CollapsibleSection
          title="System Architecture"
          id="architecture"
          expanded={expandedSections.architecture}
          toggle={toggleSection}
          sectionRef={architectureRef}
          onDownload={() => handleDownload(architectureRef, 'architecture')}
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
          sectionRef={techstackRef}
          onDownload={() => handleDownload(techstackRef, 'techstack')}
        >
          <div ref={techstackRef} className="stack-summary-grid">
            <div className="stack-summary-card">
              <h4>Languages</h4>
              <div className="stack-chip-row">
                {stackLanguages.length > 0 ? stackLanguages.map((item, idx) => (
                  <span key={`language-${item}-${idx}`} className="stack-chip language">{item}</span>
                )) : <span className="stack-chip muted">Not specified</span>}
              </div>
            </div>
            <div className="stack-summary-card">
              <h4>Frameworks</h4>
              <div className="stack-chip-row">
                {stackFrameworks.length > 0 ? stackFrameworks.map((item, idx) => (
                  <span key={`framework-${item}-${idx}`} className="stack-chip">{item}</span>
                )) : <span className="stack-chip muted">Not specified</span>}
              </div>
            </div>
            <div className="stack-summary-card">
              <h4>Modules</h4>
              <div className="stack-chip-row">
                {stackModules.length > 0 ? stackModules.map((item, idx) => (
                  <span key={`module-${item}-${idx}`} className="stack-chip module">{item}</span>
                )) : <span className="stack-chip muted">Not specified</span>}
              </div>
            </div>
          </div>

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
                {item.languages?.length > 0 && (
                  <div className="stack-meta-block">
                    <strong>Languages:</strong>
                    <div className="stack-chip-row">
                      {item.languages.map((language, languageIdx) => (
                        <span key={`${language}-${languageIdx}`} className="stack-chip language">
                          {language}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
                {item.frameworks?.length > 0 && (
                  <div className="stack-meta-block">
                    <strong>Frameworks:</strong>
                    <div className="stack-chip-row">
                      {item.frameworks.map((framework, frameworkIdx) => (
                        <span key={`${framework}-${frameworkIdx}`} className="stack-chip">
                          {framework}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
                {item.modules?.length > 0 && (
                  <div className="stack-meta-block">
                    <strong>Modules:</strong>
                    <div className="stack-chip-row">
                      {item.modules.map((moduleItem, moduleIdx) => (
                        <span key={`${moduleItem}-${moduleIdx}`} className="stack-chip module">
                          {moduleItem}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
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
          sectionRef={workflowRef}
          onDownload={() => handleDownload(workflowRef, 'workflow')}
        >
          <div ref={workflowRef} className="workflow-timeline">
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
          sectionRef={prerequisitesRef}
          onDownload={() => handleDownload(prerequisitesRef, 'prerequisites')}
        >
          <div ref={prerequisitesRef} className="prerequisites-list">
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
          sectionRef={approachesRef}
          onDownload={() => handleDownload(approachesRef, 'approaches')}
        >
          <div ref={approachesRef} className="approaches-grid">
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
          sectionRef={examplesRef}
          onDownload={() => handleDownload(examplesRef, 'examples')}
        >
          <div ref={examplesRef} className="examples-list">
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
          sectionRef={referencesRef}
          onDownload={() => handleDownload(referencesRef, 'references')}
        >
          <div ref={referencesRef} className="references-list">
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

function CollapsibleSection({ title, id, expanded, toggle, children, sectionRef, onDownload }) {
  return (
    <div className="blueprint-section">
      <div className="section-header-row">
        <button
          className="section-header"
          onClick={() => toggle(id)}
        >
          <h3 className="section-title">{title}</h3>
          {expanded ? <FiChevronUp /> : <FiChevronDown />}
        </button>
        <div className="section-actions">
          <button className="section-download small" onClick={() => onDownload && onDownload()}>Download PNG</button>
        </div>
      </div>
      {expanded && (
        <motion.div
          ref={sectionRef}
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
