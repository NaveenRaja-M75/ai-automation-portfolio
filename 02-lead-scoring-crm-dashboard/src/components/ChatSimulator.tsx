'use client';

import React, { useState, useRef, useEffect } from 'react';
import { Lead, Message } from '@/types';

interface ChatSimulatorProps {
  lead: Lead;
  messages: Message[];
  onMessageProcessed: () => void;
}

// Preset messages to facilitate quick demo testing
const DEMO_PRESETS = [
  "Hi, I'm looking for a 2BHK flat in OMR. Budget is 85 Lakhs.",
  "Planning to buy in 30 days. Ready-to-move properties.",
  "I want to discuss registration fees. Speak to a live person please.",
  "Hi, looking for a villa in Hebbal. My budget is 60 Lakhs, timeline 6 months.",
];

export default function ChatSimulator({ lead, messages, onMessageProcessed }: ChatSimulatorProps) {
  const [inputMessage, setInputMessage] = useState('');
  const [isTyping, setIsTyping] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Auto scroll to bottom when messages load/change or typing starts
  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, isTyping]);

  // Handle message sending
  const handleSendMessage = async (textToSend: string) => {
    const messageText = textToSend.trim();
    if (!messageText) return;

    setInputMessage('');
    setIsTyping(true);

    try {
      const response = await fetch('/api/chat', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          leadId: lead.id,
          phone: lead.phone,
          message: messageText,
          leadSource: lead.lead_source,
        }),
      });

      if (!response.ok) {
        throw new Error('Failed to send message to AI engine');
      }
      
      // Notify parent that a new message has been handled and we need to fetch fresh database state
      onMessageProcessed();
    } catch (error) {
      console.error('Error simulating chat:', error);
      alert('Error connecting to AI chat engine. Make sure the server is running.');
    } finally {
      setIsTyping(false);
    }
  };

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      handleSendMessage(inputMessage);
    }
  };

  return (
    <div className="bg-[#0b141a] border border-slate-800 rounded-xl flex flex-col h-[calc(100vh-140px)] shadow-xl overflow-hidden relative">
      
      {/* Simulator Header */}
      <div className="bg-[#1f2c34] px-4 py-3 flex items-center justify-between border-b border-slate-850 shrink-0">
        <div className="flex items-center gap-3">
          {/* Mock green avatar */}
          <div className="w-10 h-10 rounded-full bg-emerald-700/80 flex items-center justify-center text-white font-extrabold text-sm border border-emerald-600">
            💬
          </div>
          <div>
            <div className="text-xs font-bold text-slate-100 flex items-center gap-1.5">
              <span>{lead.name || 'Anonymous User'}</span>
              <span className="text-[10px] text-slate-400 font-mono font-normal">({lead.phone})</span>
            </div>
            <span className="text-[9px] text-emerald-400 flex items-center gap-1">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span>
              WhatsApp AI Active
            </span>
          </div>
        </div>

        <div className="text-[10px] text-slate-400 bg-slate-900 border border-slate-800 px-2 py-0.5 rounded-md font-mono">
          Simulator
        </div>
      </div>

      {/* Message Feed */}
      <div className="flex-1 overflow-y-auto p-4 space-y-3 bg-[url('https://user-images.githubusercontent.com/15075759/28719144-86dc0f70-73b1-11e7-911d-60d70fcded21.png')] bg-repeat opacity-[0.98]">
        {messages.length === 0 ? (
          <div className="h-full flex flex-col items-center justify-center p-6 text-center text-slate-500 bg-[#0b141a]/90 rounded-lg">
            <span className="text-3xl mb-2">👋</span>
            <p className="text-xs font-semibold text-slate-300">Start the Qualification Chat</p>
            <p className="text-[10px] text-slate-500 mt-1 max-w-[220px]">
              Type a message below or use a demo preset to see how the AI extracts details and scores in real-time.
            </p>
          </div>
        ) : (
          <div className="space-y-3">
            {messages.map(msg => {
              const isLead = msg.sender === 'lead';
              return (
                <div
                  key={msg.id}
                  className={`flex ${isLead ? 'justify-end' : 'justify-start'} w-full`}
                >
                  <div
                    className={`max-w-[80%] rounded-lg p-2.5 shadow-md text-xs relative ${
                      isLead
                        ? 'bg-[#005c4b] text-slate-100 rounded-tr-none'
                        : 'bg-[#202c33] text-slate-200 rounded-tl-none border border-slate-850'
                    }`}
                  >
                    <p className="whitespace-pre-line leading-relaxed font-sans">{msg.content}</p>
                    <span className="block text-[8px] text-slate-400 text-right mt-1 font-mono">
                      {new Date(msg.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {/* Typing bubble */}
        {isTyping && (
          <div className="flex justify-start w-full">
            <div className="bg-[#202c33] text-slate-300 rounded-lg p-3 rounded-tl-none border border-slate-850 shadow-md text-xs flex items-center gap-1.5">
              <span className="font-semibold text-slate-400">Gemini AI</span>
              <div className="flex gap-1">
                <span className="w-1.5 h-1.5 rounded-full bg-slate-400 animate-bounce"></span>
                <span className="w-1.5 h-1.5 rounded-full bg-slate-400 animate-bounce delay-100"></span>
                <span className="w-1.5 h-1.5 rounded-full bg-slate-400 animate-bounce delay-200"></span>
              </div>
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Presets and Inputs */}
      <div className="p-3 bg-[#1f2c34] border-t border-slate-850 shrink-0 space-y-3">
        {/* presets row */}
        <div className="space-y-1">
          <span className="text-[9px] text-slate-400 uppercase tracking-widest font-mono">Test Presets:</span>
          <div className="flex gap-1.5 overflow-x-auto pb-1 shrink-0 scrollbar-thin">
            {DEMO_PRESETS.map((preset, idx) => (
              <button
                key={idx}
                onClick={() => handleSendMessage(preset)}
                disabled={isTyping}
                className="bg-slate-900 border border-slate-800 hover:border-emerald-600 text-slate-300 text-[9px] px-2.5 py-1 rounded-full whitespace-nowrap transition-all duration-200 disabled:opacity-50"
              >
                {preset.substring(0, 35)}...
              </button>
            ))}
          </div>
        </div>

        {/* Input box */}
        <div className="flex items-center gap-2">
          <input
            type="text"
            placeholder="Type message as user..."
            value={inputMessage}
            onChange={e => setInputMessage(e.target.value)}
            onKeyPress={handleKeyPress}
            disabled={isTyping}
            className="flex-1 bg-[#2a3942] text-slate-100 placeholder-slate-400 text-xs rounded-lg py-2.5 px-4 focus:outline-none focus:ring-1 focus:ring-emerald-500 disabled:opacity-60"
          />
          <button
            onClick={() => handleSendMessage(inputMessage)}
            disabled={!inputMessage.trim() || isTyping}
            className="bg-[#00a884] hover:bg-[#008f72] text-white p-2.5 rounded-full font-bold transition-all shadow-md disabled:opacity-50"
            title="Send"
          >
            ✈️
          </button>
        </div>
      </div>

    </div>
  );
}
