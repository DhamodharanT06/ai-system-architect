import React, { useState, useRef, useEffect } from 'react';
import { motion } from 'framer-motion';
import { FiSend, FiGrid, FiMessageSquare, FiEdit3, FiMinimize2, FiMaximize2, FiShare2 } from 'react-icons/fi';
import { toBlob } from 'html-to-image';
import './App.css';
import ChatMessage from './components/ChatMessage';
import BlueprintDisplay from './components/BlueprintDisplay';
import { generateBlueprint } from './services/api';

function App() {
  const sampleIdeas = [
    'Live Mobile Tracking App',
    'Smart Delivery Fleet Panel',
    'Emergency SOS Location Hub',
    'Campus Shuttle GPS Tracker',
  ];

  const [messages, setMessages] = useState([
    {
      id: 1,
      role: 'assistant',
      content: 'Hello! I\'m your AI System Architect. I\'ll help you generate comprehensive project blueprints. Just describe your problem or project idea, and I\'ll create a complete system design with architecture, tech stack, workflow, and more!',
      timestamp: new Date(),
    },
  ]);

  const [inputValue, setInputValue] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [currentBlueprint, setCurrentBlueprint] = useState(null);
  const [showConversation, setShowConversation] = useState(true);
  const [showComposer, setShowComposer] = useState(true);
  const [shareMessage, setShareMessage] = useState('');
  const [showWelcomeDialog, setShowWelcomeDialog] = useState(true);
  const messagesEndRef = useRef(null);
  const outputCaptureRef = useRef(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  const handleShareOutput = async () => {
    if (!currentBlueprint || !outputCaptureRef.current) return;

    try {
      const blob = await toBlob(outputCaptureRef.current, {
        cacheBust: true,
        pixelRatio: 2,
        backgroundColor: '#0b1118',
      });

      if (!blob) {
        setShareMessage('Unable to generate image. Try again.');
        setTimeout(() => setShareMessage(''), 2500);
        return;
      }

      const fileName = `${(currentBlueprint.project_name || 'architecture-output').replace(/\s+/g, '-').toLowerCase()}.png`;
      const imageFile = new File([blob], fileName, { type: 'image/png' });

      if (navigator.share && navigator.canShare && navigator.canShare({ files: [imageFile] })) {
        await navigator.share({
          title: `Blueprint: ${currentBlueprint.project_name}`,
          files: [imageFile],
        });
        setShareMessage('Architecture image shared successfully.');
      } else {
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = fileName;
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(url);
        setShareMessage('Architecture image downloaded.');
      }
    } catch (error) {
      setShareMessage('Unable to share right now. Try again.');
    }

    setTimeout(() => setShareMessage(''), 2500);
  };

  const handleSendMessage = async (e) => {
    e.preventDefault();

    if (!inputValue.trim() || isLoading) return;

    // Add user message
    const userMessage = {
      id: messages.length + 1,
      role: 'user',
      content: inputValue,
      timestamp: new Date(),
    };

    setMessages((prev) => [...prev, userMessage]);
    setInputValue('');
    setIsLoading(true);

    try {
      const response = await generateBlueprint(inputValue);

      // Add assistant message
      const assistantMessage = {
        id: messages.length + 2,
        role: 'assistant',
        content: response.message.content,
        timestamp: new Date(),
        blueprint: response.blueprint,
      };

      setMessages((prev) => [...prev, assistantMessage]);
      setCurrentBlueprint(response.blueprint);
    } catch (error) {
      const errorMessage = {
        id: messages.length + 2,
        role: 'assistant',
        content: `Error: ${error.response?.data?.detail || error.message}. Please try again.`,
        timestamp: new Date(),
        isError: true,
      };
      setMessages((prev) => [...prev, errorMessage]);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="app">
      <main className="main-container">
        <header className="header">
          <h1 className="title">
            <span className="title-text">AI System Architect</span>
          </h1>
          <div className="view-controls">
            <button
              className="view-toggle"
              onClick={() => setShowConversation((prev) => !prev)}
              title={showConversation ? 'Hide conversation panel' : 'Show conversation panel'}
            >
              {showConversation ? <FiMinimize2 size={16} /> : <FiMessageSquare size={16} />}
              <span>{showConversation ? 'Focus Output' : 'Show Chat'}</span>
            </button>
            <button
              className="view-toggle"
              onClick={() => setShowComposer((prev) => !prev)}
              title={showComposer ? 'Hide input box' : 'Show input box'}
            >
              {showComposer ? <FiMinimize2 size={16} /> : <FiEdit3 size={16} />}
              <span>{showComposer ? 'Hide Input' : 'Show Input'}</span>
            </button>
          </div>
        </header>

        <div className="content-wrapper">
          <div className={`workspace-grid ${!showConversation ? 'conversation-hidden' : ''}`}>
            {showConversation && (
              <section className="chat-container panel">
                <div className="panel-head">
                  <h3>Conversation</h3>
                  <button
                    className="panel-toggle-btn"
                    onClick={() => setShowConversation(false)}
                    title="Hide conversation"
                  >
                    <FiMinimize2 size={15} />
                  </button>
                </div>
                <div className="messages-area">
                  {messages.map((msg, index) => (
                    <ChatMessage
                      key={msg.id}
                      message={msg}
                      isLast={index === messages.length - 1}
                    />
                  ))}
                  {isLoading && (
                    <motion.div
                      className="loading-indicator"
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                    >
                      <div className="typing-indicator">
                        <span></span>
                        <span></span>
                        <span></span>
                      </div>
                      <p>Generating blueprint...</p>
                    </motion.div>
                  )}
                  <div ref={messagesEndRef} />
                </div>
              </section>
            )}

            <section ref={outputCaptureRef} className={`blueprint-panel panel ${!showConversation ? 'expanded' : ''}`}>
              <div className="panel-head">
                <h3>Architecture Output</h3>
                <div className="panel-head-actions">
                  {currentBlueprint && (
                    <button
                      className="share-output-btn"
                      onClick={handleShareOutput}
                      title="Share architecture output"
                    >
                      <FiShare2 size={14} />
                      <span>Share</span>
                    </button>
                  )}
                  {!showConversation && (
                    <button
                      className="panel-toggle-btn"
                      onClick={() => setShowConversation(true)}
                      title="Show conversation"
                    >
                      <FiMaximize2 size={15} />
                    </button>
                  )}
                </div>
              </div>
              {currentBlueprint ? (
                <BlueprintDisplay
                  blueprint={currentBlueprint}
                  isFocusMode={!showConversation}
                />
              ) : (
                <div className="empty-blueprint">
                  <div className="empty-icon-wrap">
                    <FiGrid size={22} />
                  </div>
                  <h4>Your blueprint panel is ready</h4>
                  <p>Describe your idea and the generated architecture will appear here in structured panels.</p>
                  <div className="sample-grid">
                    {sampleIdeas.map((idea) => (
                      <div key={idea} className="sample-card">
                        <span>{idea}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </section>
          </div>
        </div>

        {showComposer ? (
          <div className="input-section">
            <div className="input-top-row">
              <p className="hint-text">
                Tip: Be specific and include goals, constraints, and preferences.
              </p>
              <button
                className="collapse-input-btn"
                onClick={() => setShowComposer(false)}
                title="Hide input box"
              >
                <FiMinimize2 size={14} />
                <span>Collapse</span>
              </button>
            </div>
            <form onSubmit={handleSendMessage} className="input-form">
              <input
                type="text"
                value={inputValue}
                onChange={(e) => setInputValue(e.target.value)}
                placeholder="Describe your problem or project idea..."
                disabled={isLoading}
                className="input-field"
              />
              <button
                type="submit"
                disabled={isLoading || !inputValue.trim()}
                className="send-button"
              >
                <FiSend size={20} />
              </button>
            </form>
          </div>
        ) : (
          <button
            className="restore-input-fab"
            onClick={() => setShowComposer(true)}
            title="Show input box"
          >
            <FiEdit3 size={16} />
            <span>Open Input</span>
          </button>
        )}
        {shareMessage && <div className="share-status-toast">{shareMessage}</div>}

        {showWelcomeDialog && (
          <div className="welcome-dialog-overlay" role="dialog" aria-modal="true" aria-labelledby="welcome-dialog-title">
            <div className="welcome-dialog-card">
              <h2 id="welcome-dialog-title">Welcome to AI System Architect</h2>
              <p>
                This web app helps you quickly design complete software architecture from a project idea.
                Instead of guessing the structure, you get a guided blueprint with clear implementation direction.
              </p>
              <div className="welcome-dialog-details">
                <h3>Why this is useful</h3>
                <ul>
                  <li>Converts your idea into a full system plan: architecture, workflow, and implementation steps.</li>
                  <li>Suggests practical tech stack choices with languages, frameworks, and modules.</li>
                  <li>Shows dependencies and build sequence so development can start with less confusion.</li>
                  <li>Makes collaboration easier by providing a shareable and downloadable visual output.</li>
                  <li>Helps beginners and teams move from concept to execution faster.</li>
                </ul>
              </div>
              <button
                className="welcome-dialog-ok"
                onClick={() => setShowWelcomeDialog(false)}
              >
                OK
              </button>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}

export default App;
