import React, { useState, useRef, useEffect, useCallback } from 'react';
import { Paperclip, Mic, MicOff, Send, Zap, X, FileText, Image, File, Upload } from 'lucide-react';
import { clsx } from 'clsx';
import type { UploadedFile } from '../types';
import { cn, generateId, formatFileSize, copyToClipboard } from '../lib/utils';
import { useApp } from '../store/AppContext';

interface MessageInputProps {
  onSend: (message: string, files: UploadedFile[]) => void;
  isGenerating: boolean;
  onStop: () => void;
  modelName: string;
  disabled?: boolean;
  autoFocus?: boolean;
}

const MAX_FILE_SIZE = 25 * 1024 * 1024;
const ACCEPTED_IMAGES = /^image\/(png|jpeg|jpg|gif|webp|bmp)$/i;

export function MessageInput({ onSend, isGenerating, onStop, modelName, disabled, autoFocus }: MessageInputProps) {
  const { notify, settings } = useApp();
  const [input, setInput] = useState('');
  const [files, setFiles] = useState<UploadedFile[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const [isListening, setIsListening] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const recognitionRef = useRef<any>(null);

  useEffect(() => {
    if (autoFocus) {
      setTimeout(() => textareaRef.current?.focus(), 100);
    }
  }, [autoFocus]);

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    const target = Math.min(Math.max(el.scrollHeight, 60), 280);
    el.style.height = `${target}px`;
  }, [input]);

  const handleSend = useCallback(() => {
    const trimmed = input.trim();
    if ((!trimmed && files.length === 0) || isGenerating || disabled) return;
    const hasBinary = files.some(f => ACCEPTED_IMAGES.test(f.type) || !['txt', 'json', 'csv'].includes((f.type.split('/').pop() || '')));
    if (hasBinary && files.length > 0) {
      notify('info', 'File attached', 'File content will be sent as text if readable; binary files will be referenced by name. The local backend may not process these yet.');
    }
    onSend(trimmed, files);
    setInput('');
    setFiles([]);
  }, [input, files, isGenerating, disabled, onSend, notify]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      handleSend();
    }
  };

  const addFiles = useCallback((fileList: FileList | File[]) => {
    const items = Array.from(fileList);
    const added: UploadedFile[] = [];
    for (const f of items) {
      if (f.size > MAX_FILE_SIZE) {
        notify('error', 'File too large', `${f.name} exceeds ${formatFileSize(MAX_FILE_SIZE)} limit`);
        continue;
      }
      const isText = f.type.startsWith('text/') || f.name.match(/\.(txt|md|json|csv|log|py|js|ts|tsx|jsx|html|css)$/i);
      const uf: UploadedFile = {
        id: generateId(),
        name: f.name,
        size: f.size,
        type: f.type,
        progress: isText ? 0 : 100,
      };
      if (isText) {
        const reader = new FileReader();
        reader.onprogress = (e) => {
          if (e.lengthComputable) {
            setFiles(prev => prev.map(x => x.id === uf.id ? { ...x, progress: Math.round((e.loaded / e.total) * 100) } : x));
          }
        };
        reader.onload = () => {
          const text = String(reader.result || '');
          setFiles(prev => prev.map(x => x.id === uf.id ? { ...x, progress: 100, data: text.slice(0, 80000) } : x));
        };
        reader.onerror = () => {
          setFiles(prev => prev.map(x => x.id === uf.id ? { ...x, progress: 100, error: 'Failed to read' } : x));
        };
        reader.readAsText(f);
      } else if (ACCEPTED_IMAGES.test(f.type)) {
        const reader = new FileReader();
        reader.onprogress = (e) => {
          if (e.lengthComputable) {
            setFiles(prev => prev.map(x => x.id === uf.id ? { ...x, progress: Math.round((e.loaded / e.total) * 100) } : x));
          }
        };
        reader.onload = () => {
          setFiles(prev => prev.map(x => x.id === uf.id ? { ...x, progress: 100, data: String(reader.result || '') } : x));
        };
        reader.readAsDataURL(f);
      }
      added.push(uf);
    }
    if (added.length) {
      setFiles(prev => [...prev, ...added]);
      notify('success', added.length === 1 ? 'File attached' : `${added.length} files attached`);
    }
  }, [notify]);

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    if (e.dataTransfer.files?.length) addFiles(e.dataTransfer.files);
  };

  const toggleVoice = () => {
    const SpeechRec = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SpeechRec) {
      notify('warning', 'Speech recognition not supported', 'Your browser does not support the Web Speech API.');
      return;
    }
    if (isListening) {
      recognitionRef.current?.stop();
      return;
    }
    try {
      const rec = new SpeechRec();
      rec.lang = settings.language === 'zh' ? 'zh-CN' : 'en-US';
      rec.continuous = false;
      rec.interimResults = true;
      rec.onstart = () => setIsListening(true);
      rec.onresult = (e: any) => {
        let finalText = '';
        let interimText = '';
        for (let i = e.resultIndex; i < e.results.length; i++) {
          const res = e.results[i];
          if (res.isFinal) finalText += res[0].transcript;
          else interimText += res[0].transcript;
        }
        setInput(prev => prev ? `${prev} ${finalText || interimText}`.trim() : (finalText || interimText));
        if (finalText) rec.stop();
      };
      rec.onerror = (e: any) => {
        console.warn('Speech error', e);
        if (e.error === 'not-allowed') notify('error', 'Microphone permission denied');
        setIsListening(false);
      };
      rec.onend = () => setIsListening(false);
      recognitionRef.current = rec;
      rec.start();
    } catch (err: any) {
      notify('error', 'Voice error', err?.message || 'Unable to start microphone');
    }
  };

  const onPaste = (e: React.ClipboardEvent) => {
    if (e.clipboardData.files?.length) {
      addFiles(e.clipboardData.files);
    }
  };

  const removeFile = (id: string) => {
    setFiles(prev => prev.filter(f => f.id !== id));
  };

  return (
    <div className={cn('w-full max-w-4xl mx-auto p-4', settings.animations && 'animate-in fade-in slide-in-from-bottom-2 duration-300')}>
      <div
        onDragOver={(e) => { e.preventDefault(); setIsDragging(true); }}
        onDragLeave={() => setIsDragging(false)}
        onDrop={onDrop}
        className={clsx(
          'relative flex flex-col w-full bg-[var(--card)] border rounded-2xl shadow-lg focus-within:border-primary/50 focus-within:ring-1 focus-within:ring-primary/30 transition-all',
          isDragging ? 'border-primary ring-2 ring-primary/30' : 'border-[var(--border)]'
        )}
      >
        {isDragging && (
          <div className="absolute inset-0 z-10 rounded-2xl bg-primary/5 flex items-center justify-center pointer-events-none">
            <div className="flex flex-col items-center gap-2 text-primary">
              <Upload className="w-8 h-8" />
              <div className="text-sm font-medium">Drop files here</div>
            </div>
          </div>
        )}

        {files.length > 0 && (
          <div className="px-3 pt-3 flex flex-wrap gap-2">
            {files.map(f => <FileChip key={f.id} file={f} onRemove={() => removeFile(f.id)} />)}
          </div>
        )}

        <textarea
          ref={textareaRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          onPaste={onPaste}
          placeholder={isListening ? 'Listening...' : disabled ? 'Connecting to backend...' : 'Send a message... (Shift+Enter for new line)'}
          className="w-full max-h-[280px] min-h-[60px] bg-transparent text-[var(--foreground)] placeholder-gray-500 resize-none outline-none py-4 px-4 scrollbar-hide"
          rows={1}
          disabled={disabled || isGenerating && !input}
          aria-label="Message input"
        />

        <div className="flex items-center justify-between px-3 pb-3 gap-2">
          <div className="flex items-center gap-1">
            <input
              ref={fileInputRef}
              type="file"
              multiple
              className="hidden"
              onChange={e => e.target.files && (addFiles(e.target.files), (e.target.value = ''))}
              accept=".pdf,.txt,.docx,.csv,.json,.png,.jpg,.jpeg,.gif,.webp,.md,.py,.js,.ts,.tsx,.jsx"
            />
            <button
              onClick={() => fileInputRef.current?.click()}
              disabled={isGenerating}
              className="p-2 text-gray-400 hover:text-[var(--foreground)] hover:bg-white/10 rounded-lg transition-colors disabled:opacity-40"
              aria-label="Attach files"
              title="Attach files"
            >
              <Paperclip className="w-4 h-4" />
            </button>
            <button
              onClick={toggleVoice}
              disabled={isGenerating && !isListening}
              className={clsx(
                'p-2 rounded-lg transition-colors disabled:opacity-40',
                isListening
                  ? 'text-danger bg-danger/10 hover:bg-danger/20 animate-pulse'
                  : 'text-gray-400 hover:text-[var(--foreground)] hover:bg-white/10'
              )}
              aria-label={isListening ? 'Stop recording' : 'Start voice input'}
              title={isListening ? 'Stop recording' : 'Voice input'}
            >
              {isListening ? <MicOff className="w-4 h-4" /> : <Mic className="w-4 h-4" />}
            </button>
            {isListening && <span className="ml-1 text-[10px] text-danger font-medium animate-pulse">REC</span>}
          </div>

          <div className="flex items-center gap-3 min-w-0">
            <div className="hidden sm:flex items-center gap-1.5 px-2.5 py-1 bg-[var(--surface)] border border-[var(--border)] rounded-full text-xs font-medium text-gray-400 truncate max-w-[200px]">
              <Zap className="w-3 h-3 text-accent flex-shrink-0" />
              <span className="truncate">{modelName}</span>
            </div>

            {isGenerating ? (
              <button
                onClick={onStop}
                className="p-2.5 bg-danger/20 text-danger hover:bg-danger/30 rounded-lg transition-colors flex items-center justify-center shrink-0"
                aria-label="Stop generating"
                title="Stop"
              >
                <div className="w-4 h-4 rounded-sm bg-current"></div>
              </button>
            ) : (
              <button
                onClick={handleSend}
                disabled={(!input.trim() && files.length === 0) || disabled}
                className="p-2.5 bg-primary text-white rounded-lg transition-all disabled:opacity-40 disabled:cursor-not-allowed hover:bg-primary/90 flex items-center justify-center shrink-0 shadow-md shadow-primary/20 hover:shadow-primary/40"
                aria-label="Send message"
                title="Send (Enter)"
              >
                <Send className="w-4 h-4" />
              </button>
            )}
          </div>
        </div>
      </div>
      <div className="text-center mt-2 text-xs text-gray-500">
        OmniAI can make mistakes. Verify important information. · <kbd className="px-1 py-0.5 rounded bg-white/5 border border-white/10 font-mono text-[10px]">Enter</kbd> send · <kbd className="px-1 py-0.5 rounded bg-white/5 border border-white/10 font-mono text-[10px]">Shift+Enter</kbd> newline
      </div>
    </div>
  );
}

function FileChip({ file, onRemove }: { file: UploadedFile; onRemove: () => void }) {
  const { notify } = useApp();
  const isImg = ACCEPTED_IMAGES.test(file.type);
  const Icon = isImg ? Image : (file.name.match(/\.pdf$/i) ? FileText : File);
  const done = file.progress === 100;

  return (
    <div className="group relative flex items-center gap-2 px-2.5 py-1.5 rounded-lg bg-[var(--surface)] border border-[var(--border)] text-xs max-w-[220px]">
      {isImg && done && file.data ? (
        <img src={file.data} alt="" className="w-6 h-6 rounded object-cover flex-shrink-0" />
      ) : (
        <Icon className={cn('w-4 h-4 flex-shrink-0', file.error ? 'text-danger' : 'text-primary')} />
      )}
      <div className="min-w-0 flex-1">
        <div className="font-medium truncate text-gray-200">{file.name}</div>
        <div className="text-[10px] text-gray-500">
          {file.error ? file.error : (!done ? `Processing ${file.progress}%` : formatFileSize(file.size))}
        </div>
        {!done && (
          <div className="h-0.5 w-full bg-white/10 rounded-full mt-1 overflow-hidden">
            <div className="h-full bg-primary transition-all" style={{ width: `${file.progress}%` }} />
          </div>
        )}
      </div>
      {file.data && !isImg && (
        <button
          onClick={() => copyToClipboard(file.data || '').then(() => notify('success', 'File contents copied'))}
          className="hidden group-hover:inline p-1 rounded hover:bg-white/10 text-gray-400 hover:text-white"
          title="Copy contents"
        >
          <FileText className="w-3 h-3" />
        </button>
      )}
      <button
        onClick={onRemove}
        className="p-0.5 rounded hover:bg-white/10 text-gray-400 hover:text-danger transition-colors"
        aria-label="Remove file"
        title="Remove"
      >
        <X className="w-3.5 h-3.5" />
      </button>
    </div>
  );
}
