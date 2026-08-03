import { 
  Code2, Lightbulb, FileText, Terminal, BookOpen, Database,
  Calculator, Palette, Briefcase, Send, Heart
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { clsx } from 'clsx';

interface Props {
  onPrompt?: (prompt: string) => void;
}

const suggestions: Array<{ icon: LucideIcon; title: string; prompt: string; tag?: string }> = [
  { icon: Code2, title: 'Build a React dashboard', prompt: 'Build a beautiful React dashboard UI with charts, stats cards, and a responsive sidebar. Use Tailwind CSS, TypeScript, and lucide-react icons.' },
  { icon: Lightbulb, title: 'Explain quantum computing', prompt: 'Explain quantum computing in simple terms. Compare qubits to classical bits, explain superposition and entanglement with real-world analogies, and list practical applications today.' },
  { icon: Terminal, title: 'Generate Python code', prompt: 'Write a Python script that downloads all images from a given URL, with progress reporting, error handling, and support for retries. Use requests and BeautifulSoup.' },
  { icon: FileText, title: 'Write a blog article', prompt: 'Write a blog article titled "10 Habits of Highly Effective Developers". Make it engaging, with personal anecdotes, actionable tips, and an inspiring conclusion. ~1000 words.' },
  { icon: BookOpen, title: 'Summarize a PDF', prompt: 'I will paste text from a PDF. Summarize it with: key ideas in bullet points, a 3-sentence TL;DR, and 3 critical questions for deeper discussion. First wait for me to paste the content.' },
  { icon: Database, title: 'Create SQL queries', prompt: 'You are a SQL expert. I will describe a schema and a question. Write an optimized PostgreSQL query, explain the plan, and suggest indexes if useful.' },
  { icon: Calculator, title: 'Solve math problems', prompt: 'Act as a patient math tutor. Walk me through problems step-by-step, explain the reasoning, check for common mistakes, and use LaTeX for formulas when helpful.' },
  { icon: Palette, title: 'Design critique', prompt: 'Act as a senior UI/UX designer. I will describe a screen or paste a wireframe. Give specific, actionable feedback on layout, hierarchy, color, accessibility, and micro-interactions.' },
  { icon: Briefcase, title: 'Interview prep', prompt: 'You are my interview coach for a Senior Full-Stack Engineer role. Ask me a mix of system design, algorithm, behavioral, and domain questions — then grade my answers thoroughly.' },
];

export function EmptyChat({ onPrompt }: Props) {
  return (
    <div className="relative flex-1 overflow-y-auto scrollbar-hide">
      <div className="min-h-full flex flex-col items-center justify-center px-4 sm:px-8 py-12 w-full max-w-5xl mx-auto">
        <div className="relative mb-8">
          <div className="absolute -inset-8 bg-primary/10 blur-3xl rounded-full opacity-70 pointer-events-none" />
          <div className="relative w-20 h-20 rounded-3xl bg-gradient-to-tr from-primary to-accent flex items-center justify-center ring-1 ring-primary/30 shadow-2xl shadow-primary/20">
            <span className="text-4xl font-bold text-white">O</span>
          </div>
        </div>

        <h2 className="text-3xl sm:text-4xl font-semibold text-[var(--foreground)] mb-3 text-center tracking-tight">
          What would you like to accomplish today?
        </h2>
        <p className="text-sm sm:text-base text-gray-500 mb-12 text-center max-w-xl">
          Start with a prompt below, or describe your own idea. OmniAI streams responses in real time.
        </p>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 sm:gap-4 w-full">
          {suggestions.map((item, index) => (
            <button
              key={index}
              onClick={() => onPrompt?.(item.prompt)}
              className={clsx(
                'group relative overflow-hidden flex flex-col gap-2 p-4 rounded-2xl border bg-white/[0.02] hover:bg-white/[0.06] hover:border-primary/30 transition-all text-left',
                'border-[var(--border)]'
              )}
              style={{ animationDelay: `${index * 30}ms` }}
            >
              <div className="flex items-center gap-3">
                <div className="w-9 h-9 rounded-xl bg-[var(--surface)] group-hover:bg-primary/15 border border-[var(--border)] flex items-center justify-center text-gray-400 group-hover:text-primary transition-all">
                  <item.icon className="w-4.5 h-4.5" style={{ width: 18, height: 18 }} />
                </div>
                <span className="text-sm font-medium text-[var(--foreground)] group-hover:text-primary transition-colors">
                  {item.title}
                </span>
              </div>
              <p className="text-xs text-gray-500 line-clamp-2 leading-relaxed pl-12">
                {item.prompt}
              </p>
              <div className="absolute right-3 top-3 opacity-0 group-hover:opacity-100 transition-all translate-x-1 group-hover:translate-x-0">
                <Send className="w-4 h-4 text-primary" />
              </div>
            </button>
          ))}
        </div>

        <div className="mt-16 flex items-center gap-2 text-[11px] text-gray-600">
          <Heart className="w-3 h-3" />
          <span>Built with Gemini · Streaming · Markdown · Voice</span>
        </div>
      </div>
    </div>
  );
}

export function WelcomeScreen({ onStart }: { onStart: () => void }) {
  return (
    <div className="flex-1 flex flex-col items-center justify-center p-8 w-full h-full text-center relative overflow-hidden">
      <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[800px] h-[800px] bg-primary/5 rounded-full blur-3xl pointer-events-none"></div>
      <div className="relative z-10 max-w-2xl flex flex-col items-center">
        <div className="w-20 h-20 rounded-3xl bg-gradient-to-tr from-primary to-accent flex items-center justify-center mb-8 shadow-[0_0_60px_-15px_rgba(37,99,235,0.5)]">
          <span className="text-4xl font-bold text-white">O</span>
        </div>
        <h1 className="text-5xl font-bold text-[var(--foreground)] mb-4 tracking-tight">
          Omni<span className="text-primary">AI</span>
        </h1>
        <h2 className="text-2xl font-medium text-gray-300 mb-6">
          One Platform. Every AI.
        </h2>
        <p className="text-lg text-gray-400 mb-12 max-w-xl leading-relaxed">
          Choose the best AI model for every task—from coding and research to writing and brainstorming—all in one elegant workspace.
        </p>
        <div className="flex items-center gap-4">
          <button
            onClick={onStart}
            className="px-8 py-4 bg-primary hover:bg-primary/90 text-white rounded-xl font-semibold transition-all shadow-[0_0_30px_-10px_rgba(37,99,235,0.5)] hover:shadow-[0_0_40px_-10px_rgba(37,99,235,0.7)] hover:-translate-y-0.5"
          >
            Start Chatting
          </button>
          <button onClick={onStart} className="px-8 py-4 bg-white/5 hover:bg-white/10 text-[var(--foreground)] rounded-xl font-medium border border-[var(--border)] transition-all hover:border-white/20">
            Explore Models
          </button>
        </div>
      </div>
    </div>
  );
}
