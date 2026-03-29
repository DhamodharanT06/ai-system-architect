import React, { useState, useRef, useEffect } from 'react';
import { motion } from 'framer-motion';
import { FiSend, FiMenu, FiX, FiGrid } from 'react-icons/fi';
import './App.css';
import ChatMessage from './components/ChatMessage';
import BlueprintDisplay from './components/BlueprintDisplay';
import Sidebar from './components/Sidebar';
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
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [currentBlueprint, setCurrentBlueprint] = useState(null);
  const messagesEndRef = useRef(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

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
      <Sidebar isOpen={sidebarOpen} onClose={() => setSidebarOpen(false)} />

      <main className="main-container">
        <header className="header">
          <button
            className="menu-toggle"
            onClick={() => setSidebarOpen(!sidebarOpen)}
          >
            {sidebarOpen ? <FiX size={24} /> : <FiMenu size={24} />}
          </button>
          <h1 className="title">
            <span className="title-text">AI System Architect</span>
          </h1>
          <div className="header-spacer" />
        </header>

        <div className="content-wrapper">
          <div className="workspace-grid">
            <section className="chat-container panel">
              <div className="panel-head">
                <h3>Conversation</h3>
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

            <section className="blueprint-panel panel">
              <div className="panel-head">
                <h3>Architecture Output</h3>
              </div>
              {currentBlueprint ? (
                <BlueprintDisplay blueprint={currentBlueprint} />
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

        <div className="input-section">
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
          <p className="hint-text">
            💡 Tip: Be specific! Include your goals, constraints, and any preferences.
          </p>
        </div>
      </main>
    </div>
  );
}

export default App;
