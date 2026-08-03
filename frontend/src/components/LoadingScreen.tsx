import { WifiOff, RefreshCw, ServerCog } from 'lucide-react';
import { useApp } from '../store/AppContext';

interface Props {
  onRetry: () => void;
  retryCount: number;
  nextRetrySec: number;
}

export function BackendOfflineScreen({ onRetry, retryCount, nextRetrySec }: Props) {
  const { notify } = useApp();
  return (
    <div className="fixed inset-0 z-50 bg-[var(--background)] flex items-center justify-center p-6">
      <div className="max-w-md w-full text-center">
        <div className="relative mx-auto w-24 h-24 mb-6">
          <div className="absolute inset-0 bg-danger/10 rounded-3xl animate-pulse" />
          <div className="absolute inset-0 flex items-center justify-center">
            <div className="w-16 h-16 rounded-2xl bg-danger/20 flex items-center justify-center">
              <WifiOff className="w-8 h-8 text-danger" />
            </div>
          </div>
        </div>
        <h1 className="text-2xl font-bold text-[var(--foreground)] mb-2">Cannot reach backend</h1>
        <p className="text-gray-400 text-sm mb-8 leading-relaxed">
          OmniAI couldn't connect to the Gemini API server at <code className="px-1.5 py-0.5 rounded bg-white/5 text-primary text-xs font-mono">http://localhost:8081/v1</code>.
          Make sure the backend is running, or wait for auto-reconnect.
        </p>
        <div className="flex flex-col sm:flex-row gap-3 justify-center">
          <button
            onClick={() => { notify('info', 'Reconnecting...'); onRetry(); }}
            className="px-5 py-3 rounded-xl bg-primary text-white hover:bg-primary/90 transition-all flex items-center justify-center gap-2 font-medium shadow-md shadow-primary/20"
          >
            <RefreshCw className="w-4 h-4" />
            Retry now
          </button>
          <a
            href="https://github.com/Sophomoresty/gemini-web2api"
            target="_blank" rel="noreferrer"
            className="px-5 py-3 rounded-xl bg-white/5 hover:bg-white/10 border border-white/10 text-gray-200 transition-colors flex items-center justify-center gap-2 font-medium"
          >
            <ServerCog className="w-4 h-4" />
            Setup backend docs
          </a>
        </div>
        <div className="mt-8 p-4 rounded-xl bg-white/5 border border-white/10 text-left">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-medium text-gray-400">Auto-reconnect</span>
            <span className="text-xs font-mono text-[var(--foreground)]">
              {nextRetrySec > 0 ? `Retrying in ${nextRetrySec}s` : 'Retrying now...'}
            </span>
          </div>
          <div className="w-full h-1.5 bg-white/10 rounded-full overflow-hidden">
            <div
              className="h-full bg-primary transition-all duration-1000"
              style={{ width: `${Math.min(100, (retryCount / 5) * 100)}%` }}
            />
          </div>
          <div className="mt-2 text-[10px] text-gray-500">
            Attempt {retryCount + 1} · Backoff: {(Math.min(retryCount * 2, 15) || 2)}s
          </div>
        </div>
        <div className="mt-6 text-[11px] text-gray-600">
          Tip: Start the backend with <code className="px-1 rounded bg-white/5 font-mono">python -m gemini_web2api</code> in the backend folder.
        </div>
      </div>
    </div>
  );
}

export function SkeletonMessages() {
  return (
    <div className="max-w-4xl mx-auto space-y-8 p-4 mt-6">
      {[0, 1].map(i => (
        <div key={i} className={`flex gap-4 ${i % 2 === 1 ? 'justify-end' : 'justify-start'}`}>
          <div className={`w-8 h-8 rounded-lg bg-white/10 animate-pulse ${i % 2 === 1 ? 'order-2 rounded-full' : ''}`} />
          <div className="max-w-[70%] w-full space-y-2">
            <div className={`h-3 w-24 bg-white/10 rounded animate-pulse ${i % 2 === 1 ? 'ml-auto' : ''}`} />
            <div className="h-4 w-full bg-white/5 rounded animate-pulse" />
            <div className="h-4 w-5/6 bg-white/5 rounded animate-pulse" />
            <div className="h-4 w-2/3 bg-white/5 rounded animate-pulse" />
          </div>
        </div>
      ))}
    </div>
  );
}

export function NewChatSkeleton() {
  return (
    <div className="h-full flex flex-col items-center justify-center p-8 max-w-4xl mx-auto mt-16 gap-6">
      <div className="w-16 h-16 rounded-2xl bg-white/5 animate-pulse ring-1 ring-white/10" />
      <div className="h-8 w-96 bg-white/5 rounded-xl animate-pulse" />
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 w-full">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="h-20 rounded-xl bg-white/5 animate-pulse border border-white/5" style={{ animationDelay: `${i * 80}ms` }} />
        ))}
      </div>
    </div>
  );
}
