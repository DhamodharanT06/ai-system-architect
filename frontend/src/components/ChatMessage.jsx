import React from 'react';
import { motion } from 'framer-motion';
import { FiCopy, FiCheck } from 'react-icons/fi';
import './ChatMessage.css';

function ChatMessage({ message, isLast }) {
  const [copied, setCopied] = React.useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(message.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <motion.div
      className={`message ${message.role}`}
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
    >
      <div className={`message-content ${message.isError ? 'error' : ''}`}>
        {message.content}
        {message.role === 'assistant' && (
          <button
            className="copy-button"
            onClick={handleCopy}
            title="Copy message"
          >
            {copied ? <FiCheck size={16} /> : <FiCopy size={16} />}
          </button>
        )}
      </div>
      {message.timestamp && (
        <span className="message-time">
          {new Date(message.timestamp).toLocaleTimeString([], {
            hour: '2-digit',
            minute: '2-digit',
          })}
        </span>
      )}
    </motion.div>
  );
}

export default ChatMessage;
