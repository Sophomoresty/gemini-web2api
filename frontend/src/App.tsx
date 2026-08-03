import { useEffect, useRef, useState, useCallback } from 'react';
import { Sidebar } from './components/Sidebar';
import { TopBar } from './components/TopBar';
import { EmptyChat } from './components/EmptyChat';
import { MessageInput } from './components/MessageInput';
import { ChatWindow } from './components/ChatWindow';
import { BackendOfflineScreen, SkeletonMessages } from './components/LoadingScreen';
import { ProfileDialog, SettingsDialog } from './components/SettingsDialogs';
import { useApp } from './store/AppContext';
import { streamChatCompletion, generateTitle, checkBackendHealth } from './lib/api';
import { generateId } from './lib/utils';
import type { Conversation, UploadedFile, Message } from './types';

const MODEL_NAME_MAP: Record<string, string> = {
  'gemini-3.6-flash': 'Gemini 3.6 Flash',
  'gemini-3.5-flash': 'Gemini 3.5 Flash',
  'gemini-3.5-flash-thinking': 'Gemini Flash Thinking',
  'gemini-flash-lite': 'Gemini Flash Lite',
  'gemini-auto': 'Gemini Auto',
};

function App() {
  const {
    conversations, currentChatId, selectedModel, settings, backendAvailable, dialog,
    dispatch, createNewChat, notify, openDialog, closeDialogs, getCurrentConversation,
  } = useApp();

  const [isGenerating, setIsGenerating] = useState(false);
  const [appLoading, setAppLoading] = useState(true);
  const [retryCount, setRetryCount] = useState(0);
  const [nextRetrySec, setNextRetrySec] = useState(0);
  const [inputRefPrompt, setInputRefPrompt] = useState<string | null>(null);
  const [autoFocusCounter, setAutoFocusCounter] = useState(0);
  const abortRef = useRef<AbortController | null>(null);
  const generatingChatIdRef = useRef<string | null>(null);

  // Startup: show splash briefly, then do backend health
  useEffect(() => {
    const splash = setTimeout(() => setAppLoading(false), 700);
    return () => clearTimeout(splash);
  }, []);

  // Backend health polling + auto-retry backoff
  useEffect(() => {
    let cancelled = false;
    let pollTimer: ReturnType<typeof setTimeout>;
    let countdownTimer: ReturnType<typeof setInterval>;

    const poll = async () => {
      const ok = await checkBackendHealth();
      if (!cancelled) {
        dispatch({ type: 'SET_BACKEND_AVAILABLE', payload: ok });
        if (!ok) {
          const backoff = Math.min(2 + retryCount * 2, 15);
          setNextRetrySec(backoff);
          setRetryCount(r => r + 1);
          countdownTimer = setInterval(() => {
            setNextRetrySec(n => {
              if (n <= 1) { clearInterval(countdownTimer); return 0; }
              return n - 1;
            });
          }, 1000);
          pollTimer = setTimeout(poll, backoff * 1000);
        } else {
          setRetryCount(0);
          setNextRetrySec(0);
          pollTimer = setTimeout(poll, 15000); // keep alive poll
        }
      }
    };

    const initial = setTimeout(poll, 800);
    return () => {
      cancelled = true;
      clearTimeout(initial);
      clearTimeout(pollTimer);
      clearInterval(countdownTimer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [retryCount > 0 ? false : 'once']);

  // Keyboard shortcuts
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = e.ctrlKey || e.metaKey;
      if (mod && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        openDialog({ search: true });
        return;
      }
      if (mod && e.key.toLowerCase() === 'n') {
        e.preventDefault();
        createNewChat();
        setAutoFocusCounter(c => c + 1);
        return;
      }
      if (mod && e.shiftKey && (e.key === 'L' || e.key === 'l')) {
        e.preventDefault();
        dispatch({ type: 'TOGGLE_SIDEBAR' });
        return;
      }
      if (e.key === 'Escape') {
        const anyOpen = Object.values(dialog).some(v => v === true || (typeof v === 'string' && v));
        if (anyOpen) { closeDialogs(); return; }
        if (isGenerating) handleStop();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dialog, isGenerating]);

  // If no current chat and there are conversations, open the most recent one
  useEffect(() => {
    if (!currentChatId && conversations.length > 0) {
      const latest = [...conversations].sort((a, b) => b.updatedAt - a.updatedAt)[0];
      dispatch({ type: 'SET_CURRENT_CHAT', payload: latest.id });
    }
  }, [currentChatId, conversations, dispatch]);

  const handleStop = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setIsGenerating(false);
    notify('info', 'Generation stopped');
  }, [notify]);

  // Send (handles first message, existing chats, file attachments)
  const handleSend = useCallback(async (rawContent: string, files: UploadedFile[]) => {
    if (!backendAvailable) { notify('error', 'Backend offline', 'Wait for reconnect or start the gemini-web2api server.'); return; }
    if (isGenerating || !rawContent.trim()) return;

    let chatId = currentChatId;
    let conv: Conversation | null = chatId ? conversations.find(c => c.id === chatId) || null : null;

    // Create new chat if none
    if (!conv) {
      chatId = createNewChat(selectedModel);
      conv = conversations.find(c => c.id === chatId) || null;
      if (!conv) return;
    } else {
      // Update conversation model to currently selected if changed
      if (conv.model !== selectedModel) {
        dispatch({ type: 'UPDATE_CONVERSATION', payload: { id: conv.id, updates: { model: selectedModel } } });
      }
    }

    // Build user content (prepend readable file data)
    let content = rawContent;
    const textFiles = files.filter(f => f.data && !f.type.startsWith('image/'));
    const images = files.filter(f => f.type.startsWith('image/') && f.data);
    if (textFiles.length > 0) {
      const fileParts = textFiles.map(f => {
        const truncated = (f.data || '').slice(0, 40000);
        return `\n\n--- FILE: ${f.name} ---\n${truncated}${(f.data || '').length > 40000 ? '\n... (truncated)' : ''}\n--- END FILE ---`;
      }).join('\n');
      content = `${content}\n${fileParts}`;
    }
    if (images.length > 0) {
      notify('info', 'Images attached', 'This backend may not process images. They are referenced but may be ignored.');
    }

    const userMsgId = generateId();
    const userMsg: Message = {
      id: userMsgId,
      role: 'user',
      content,
      timestamp: Date.now(),
    };
    dispatch({ type: 'ADD_MESSAGE', payload: { chatId: chatId!, message: userMsg } });

    // Title generation: first user message (async, non-blocking)
    const wasEmpty = conv.messages.length === 0;
    if (wasEmpty && rawContent.length > 0) {
      const forTitle = rawContent.slice(0, 200);
      setTimeout(() => {
        generateTitle(forTitle, selectedModel).then(title => {
          dispatch({ type: 'UPDATE_CONVERSATION', payload: { id: chatId!, updates: { title } } });
        });
      }, 300);
    }

    const assistantMsgId = generateId();
    const initialAssistant: Message = {
      id: assistantMsgId,
      role: 'assistant',
      content: '',
      timestamp: Date.now(),
      thinking: true,
      renderMode: 'markdown',
    };
    dispatch({ type: 'ADD_MESSAGE', payload: { chatId: chatId!, message: initialAssistant } });

    // Load conversation for context
    const currentConv = conversations.find(c => c.id === chatId) || getCurrentConversation();
    const allMessages = [...(currentConv?.messages || []), userMsg];
    // filter to messages only (no assistant with empty)
    const forApi = allMessages.filter(m => !(m.role === 'assistant' && m.content === '')).map(m => ({
      id: m.id, role: m.role, content: m.content, timestamp: m.timestamp,
    }));

    setIsGenerating(true);
    generatingChatIdRef.current = chatId;
    const ctrl = new AbortController();
    abortRef.current = ctrl;

    const modelSettings = settings.modelDefaults;

    try {
      const { content: finalContent, usage } = await streamChatCompletion(
        forApi,
        selectedModel,
        {
          temperature: modelSettings.temperature,
          maxTokens: modelSettings.maxTokens,
          topP: modelSettings.topP,
          stream: settings.streaming,
        },
        (textDelta, reasoningDelta) => {
          if (ctrl.signal.aborted) return;
          // Update streaming content and reasoning
          const state = useMyState();
          const cur = (state as any).getAssistantContent(chatId!, assistantMsgId);
          const newContent = cur.content + textDelta;
          const newReasoning = cur.reasoning + reasoningDelta;
          (state as any).updateAssistant(chatId!, assistantMsgId, newContent, newReasoning);
        },
        ctrl.signal,
      );
      if (!ctrl.signal.aborted) {
        dispatch({
          type: 'UPDATE_MESSAGE',
          payload: {
            chatId: chatId!,
            messageId: assistantMsgId,
            updates: {
              thinking: false,
              content: finalContent || initialAssistant.content || '_No response received_',
              renderMode: 'markdown',
              ...(usage ? { tokenUsage: usage } : {}),
            },
          },
        });
        if (usage && settings.notifications.enabled) notify('success', 'Response complete', `${usage.totalTokens.toLocaleString()} tokens used`);
      }
    } catch (err: any) {
      if (err?.name === 'AbortError') {
        dispatch({ type: 'UPDATE_MESSAGE', payload: { chatId: chatId!, messageId: assistantMsgId, updates: { thinking: false } } });
      } else {
        const msg = err?.message || String(err);
        dispatch({
          type: 'UPDATE_MESSAGE',
          payload: {
            chatId: chatId!,
            messageId: assistantMsgId,
            updates: {
              thinking: false,
              error: true,
              content: `${initialAssistant.content}\n\n**Error:** ${msg}`,
              renderMode: 'markdown',
            },
          },
        });
        notify('error', 'Request failed', msg);
      }
    } finally {
      setIsGenerating(false);
      abortRef.current = null;
      generatingChatIdRef.current = null;
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentChatId, conversations, selectedModel, isGenerating, backendAvailable, settings.streaming, settings.modelDefaults, settings.notifications.enabled]);

  // Simple imperative state helpers for streaming (avoid stale closures via reducer)
  const useMyState = () => ({
    getAssistantContent(cid: string, mid: string) {
      const conv = conversations.find(c => c.id === cid);
      const m = conv?.messages.find(x => x.id === mid);
      return { content: m?.content || '', reasoning: (m as any)?.reasoning || '' };
    },
    updateAssistant(cid: string, mid: string, content: string, reasoning: string) {
      dispatch({
        type: 'UPDATE_MESSAGE',
        payload: {
          chatId: cid,
          messageId: mid,
          updates: {
            content,
            thinking: true,
            renderMode: 'markdown',
            ...(reasoning ? { reasoning } as any : {}),
          },
        },
      });
    },
  });

  // Action handlers
  const onRegenerate = useCallback(async (messageId: string) => {
    if (!currentChatId || isGenerating || !backendAvailable) return;
    const conv = getCurrentConversation();
    if (!conv) return;
    const idx = conv.messages.findIndex(m => m.id === messageId);
    if (idx <= 0) return;
    // Find prior user message
    let userIdx = idx - 1;
    while (userIdx >= 0 && conv.messages[userIdx].role !== 'user') userIdx--;
    if (userIdx < 0) return;
    const prior = conv.messages.slice(0, userIdx + 1);

    // Remove assistant message onwards
    dispatch({ type: 'SET_MESSAGES', payload: { chatId: currentChatId, messages: prior } });

    // Remove the thinking flag/content from existing flow by calling internal re-stream
    const assistantMsgId = generateId();
    dispatch({
      type: 'ADD_MESSAGE',
      payload: { chatId: currentChatId!, message: { id: assistantMsgId, role: 'assistant', content: '', timestamp: Date.now(), thinking: true, renderMode: 'markdown' } },
    });
    setIsGenerating(true);
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    generatingChatIdRef.current = currentChatId;
    try {
      const { content: finalContent, usage } = await streamChatCompletion(
        prior.map(m => ({ ...m })),
        conv.model || selectedModel,
        {
          temperature: settings.modelDefaults.temperature,
          maxTokens: settings.modelDefaults.maxTokens,
          topP: settings.modelDefaults.topP,
          stream: settings.streaming,
        },
        (textDelta) => {
          const state = useMyState();
          const cur = (state as any).getAssistantContent(currentChatId, assistantMsgId);
          (state as any).updateAssistant(currentChatId, assistantMsgId, cur.content + textDelta, '');
        },
        ctrl.signal,
      );
      dispatch({
        type: 'UPDATE_MESSAGE',
        payload: {
          chatId: currentChatId!, messageId: assistantMsgId,
          updates: { thinking: false, content: finalContent || '(empty)', renderMode: 'markdown', ...(usage ? { tokenUsage: usage } : {}) },
        },
      });
      notify('success', 'Response regenerated');
    } catch (err: any) {
      if (err?.name !== 'AbortError') {
        dispatch({
          type: 'UPDATE_MESSAGE',
          payload: {
            chatId: currentChatId!, messageId: assistantMsgId,
            updates: { thinking: false, error: true, content: `**Error:** ${err?.message || 'Failed to regenerate'}`, renderMode: 'markdown' },
          },
        });
        notify('error', 'Regeneration failed', err?.message);
      }
    } finally {
      setIsGenerating(false);
      abortRef.current = null;
      generatingChatIdRef.current = null;
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentChatId, isGenerating, backendAvailable, selectedModel, settings.streaming]);

  const onRetry = useCallback(async (messageId: string) => {
    // Retry = regenerate from the previous user message
    onRegenerate(messageId);
  }, [onRegenerate]);

  const onEditSubmit = useCallback(async (messageId: string, newContent: string) => {
    if (!currentChatId || isGenerating) return;
    const conv = getCurrentConversation();
    if (!conv) return;
    const idx = conv.messages.findIndex(m => m.id === messageId);
    if (idx < 0 || conv.messages[idx].role !== 'user') return;
    // Remove everything from idx forward
    const kept = conv.messages.slice(0, idx);
    const editedMsg: Message = { ...conv.messages[idx], content: newContent, edited: true, timestamp: Date.now() };
    dispatch({ type: 'SET_MESSAGES', payload: { chatId: currentChatId, messages: [...kept, editedMsg] } });

    // Re-generate assistant
    const assistantMsgId = generateId();
    dispatch({
      type: 'ADD_MESSAGE',
      payload: { chatId: currentChatId!, message: { id: assistantMsgId, role: 'assistant', content: '', timestamp: Date.now(), thinking: true, renderMode: 'markdown' } },
    });
    setIsGenerating(true);
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    generatingChatIdRef.current = currentChatId;
    try {
      const msgs = [...kept, editedMsg];
      const { content: finalContent, usage } = await streamChatCompletion(
        msgs,
        conv.model || selectedModel,
        {
          temperature: settings.modelDefaults.temperature,
          maxTokens: settings.modelDefaults.maxTokens,
          topP: settings.modelDefaults.topP,
          stream: settings.streaming,
        },
        (textDelta) => {
          const state = useMyState();
          const cur = (state as any).getAssistantContent(currentChatId!, assistantMsgId);
          (state as any).updateAssistant(currentChatId!, assistantMsgId, cur.content + textDelta, '');
        },
        ctrl.signal,
      );
      dispatch({
        type: 'UPDATE_MESSAGE',
        payload: {
          chatId: currentChatId!, messageId: assistantMsgId,
          updates: { thinking: false, content: finalContent || '(empty)', renderMode: 'markdown', ...(usage ? { tokenUsage: usage } : {}) },
        },
      });
      notify('success', 'Message updated');
    } catch (err: any) {
      if (err?.name !== 'AbortError') {
        dispatch({
          type: 'UPDATE_MESSAGE',
          payload: {
            chatId: currentChatId!, messageId: assistantMsgId,
            updates: { thinking: false, error: true, content: `**Error:** ${err?.message || 'Failed'}`, renderMode: 'markdown' },
          },
        });
      }
    } finally {
      setIsGenerating(false);
      abortRef.current = null;
      generatingChatIdRef.current = null;
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentChatId, isGenerating, selectedModel]);

  const onDelete = useCallback((messageId: string) => {
    if (!currentChatId) return;
    dispatch({ type: 'DELETE_MESSAGE', payload: { chatId: currentChatId, messageId } });
    notify('info', 'Message deleted');
  }, [currentChatId, dispatch, notify]);

  const onForceBackendCheck = useCallback(async () => {
    setNextRetrySec(0);
    const ok = await checkBackendHealth();
    dispatch({ type: 'SET_BACKEND_AVAILABLE', payload: ok });
    if (ok) {
      setRetryCount(0);
      notify('success', 'Backend connection restored');
    }
  }, [dispatch, notify]);

  const currentConv = getCurrentConversation();
  const messages = currentConv?.messages || [];
  const isNew = messages.length === 0;

  const showOffline = !backendAvailable && !appLoading;

  if (appLoading) {
    return (
      <div className="fixed inset-0 bg-[var(--background)] flex items-center justify-center">
        <div className="relative flex flex-col items-center gap-4">
          <div className="relative w-16 h-16">
            <div className="absolute inset-0 bg-primary/20 rounded-2xl animate-pulse" />
            <div className="absolute inset-0 rounded-2xl bg-gradient-to-tr from-primary to-accent flex items-center justify-center shadow-xl animate-bounce">
              <span className="text-3xl font-bold text-white">O</span>
            </div>
          </div>
          <div className="text-sm text-gray-400 font-medium">Loading OmniAI...</div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-screen bg-[var(--background)] text-[var(--foreground)] font-sans overflow-hidden transition-colors">
      <Sidebar />
      <main className="flex-1 flex flex-col min-w-0 min-h-0 w-0">
        <TopBar />
        <div className="flex-1 flex flex-col min-h-0 relative">
          {appLoading ? (
            <SkeletonMessages />
          ) : isNew && currentChatId ? (
            <>
              <EmptyChat onPrompt={(text) => { setInputRefPrompt(text); setAutoFocusCounter(c => c + 1); }} />
              <div className="shrink-0 pt-2 pb-4 bg-gradient-to-t from-[var(--background)] via-[var(--background)] to-transparent">
                <MessageInput
                  key={`mi-${autoFocusCounter}`}
                  onSend={(text, files) => { handleSend(text, files); }}
                  isGenerating={isGenerating}
                  onStop={handleStop}
                  modelName={MODEL_NAME_MAP[selectedModel] || 'Gemini'}
                  disabled={!backendAvailable}
                  autoFocus
                />
                {/* use inputRefPrompt as seed via useEffect below - use a ref via callback pattern by using initial state */}
                <SeedTextSetter seed={inputRefPrompt} key={`seed-${autoFocusCounter}`} onDone={() => setInputRefPrompt(null)} />
              </div>
            </>
          ) : (
            <>
              <ChatWindow
                messages={messages}
                isGenerating={isGenerating}
                onRegenerate={onRegenerate}
                onRetry={onRetry}
                onEditSubmit={onEditSubmit}
                onDelete={onDelete}
                chatId={currentChatId!}
              />
              <div className="shrink-0 pt-2 pb-4 bg-gradient-to-t from-[var(--background)] via-[var(--background)] to-transparent">
                <MessageInput
                  key={`mi-${autoFocusCounter}`}
                  onSend={handleSend}
                  isGenerating={isGenerating}
                  onStop={handleStop}
                  modelName={MODEL_NAME_MAP[selectedModel] || 'Gemini'}
                  disabled={!backendAvailable}
                  autoFocus
                />
                <SeedTextSetter seed={inputRefPrompt} key={`seed-${autoFocusCounter}`} onDone={() => setInputRefPrompt(null)} />
              </div>
            </>
          )}
        </div>
      </main>

      {showOffline && (
        <BackendOfflineScreen
          onRetry={onForceBackendCheck}
          retryCount={retryCount}
          nextRetrySec={nextRetrySec}
        />
      )}

      <SettingsDialog open={dialog.settings} onClose={closeDialogs} />
      <ProfileDialog open={dialog.profile} onClose={closeDialogs} />
    </div>
  );
}

// Tiny helper component to set textarea initial value on new key
function SeedTextSetter({ seed, onDone }: { seed: string | null; onDone: () => void }) {
  useEffect(() => {
    if (!seed) return;
    const ta = document.querySelector<HTMLTextAreaElement>('textarea[placeholder*="Send a message"]')
      || document.querySelector<HTMLTextAreaElement>('textarea');
    if (ta) {
      ta.value = seed;
      ta.dispatchEvent(new Event('input', { bubbles: true }));
      ta.focus();
      ta.setSelectionRange(ta.value.length, ta.value.length);
    }
    const t = setTimeout(onDone, 100);
    return () => clearTimeout(t);
  }, [seed, onDone]);
  return null;
}

export default App;
