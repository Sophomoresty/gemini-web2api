import { useState, useRef, useEffect } from 'react';
import { ChevronDown, Zap, Sparkles, Code2, Brain, Check, Lock } from 'lucide-react';
import clsx from 'clsx';

export type Model = {
  id: string;
  name: string;
  provider: string;
  description: string;
  speed: number;
  reasoning: number;
  coding: number;
  badge?: string;
  disabled?: boolean;
};

const models: Model[] = [
  {
    id: 'gemini-3.6-flash',
    name: 'Gemini 3.6 Flash',
    provider: 'Google',
    description: 'Next-gen fast model for general tasks',
    speed: 98,
    reasoning: 85,
    coding: 88,
    badge: 'New'
  },
  {
    id: 'gemini-3.5-flash',
    name: 'Gemini 3.5 Flash',
    provider: 'Google',
    description: 'Fast and versatile model',
    speed: 95,
    reasoning: 80,
    coding: 82
  },
  {
    id: 'gemini-3.5-flash-thinking',
    name: 'Gemini Flash Thinking',
    provider: 'Google',
    description: 'Advanced reasoning and problem solving',
    speed: 80,
    reasoning: 98,
    coding: 95,
    badge: 'Default'
  },
  {
    id: 'gemini-flash-lite',
    name: 'Gemini Flash Lite',
    provider: 'Google',
    description: 'Ultra-fast lightweight responses',
    speed: 100,
    reasoning: 70,
    coding: 75
  },
  {
    id: 'gemini-auto',
    name: 'Gemini Auto',
    provider: 'Google',
    description: 'Automatically routes to the best model',
    speed: 90,
    reasoning: 90,
    coding: 90,
    badge: 'Smart'
  },
  // Future Models
  {
    id: 'gpt-4o',
    name: 'GPT-4o',
    provider: 'OpenAI',
    description: 'Powerful multimodal capabilities',
    speed: 85,
    reasoning: 95,
    coding: 96,
    disabled: true
  },
  {
    id: 'claude-3-5-sonnet',
    name: 'Claude 3.5 Sonnet',
    provider: 'Anthropic',
    description: 'Exceptional coding and writing',
    speed: 90,
    reasoning: 96,
    coding: 98,
    disabled: true
  },
  {
    id: 'deepseek-coder',
    name: 'DeepSeek Coder',
    provider: 'DeepSeek',
    description: 'Specialized for development',
    speed: 88,
    reasoning: 85,
    coding: 99,
    disabled: true
  },
  {
    id: 'qwen-max',
    name: 'Qwen Max',
    provider: 'Alibaba',
    description: 'Top-tier open weights model',
    speed: 85,
    reasoning: 90,
    coding: 88,
    disabled: true
  },
  {
    id: 'mistral-large',
    name: 'Mistral Large',
    provider: 'Mistral AI',
    description: 'Top-tier reasoning and logic',
    speed: 85,
    reasoning: 92,
    coding: 89,
    disabled: true
  },
  {
    id: 'llama-3',
    name: 'Llama 3',
    provider: 'Meta',
    description: 'Fast open source model',
    speed: 92,
    reasoning: 85,
    coding: 85,
    disabled: true
  }
];

export function ModelSelector({ 
  currentModelId, 
  onSelect 
}: { 
  currentModelId: string, 
  onSelect: (id: string) => void 
}) {
  const [isOpen, setIsOpen] = useState(false);
  const currentModel = models.find(m => m.id === currentModelId) || models[2];
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (ref.current && !ref.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  return (
    <div className="relative" ref={ref}>
      <button 
        onClick={() => setIsOpen(!isOpen)}
        className="flex items-center gap-2 px-3 py-1.5 rounded-lg hover:bg-white/5 transition-colors text-sm font-medium text-gray-200"
      >
        <span className="text-gray-400">Model:</span>
        <span>{currentModel.name}</span>
        <ChevronDown className="w-4 h-4 text-gray-500" />
      </button>

      {isOpen && (
        <div className="absolute top-full left-0 mt-2 w-[400px] bg-[#18181B] border border-white/10 rounded-xl shadow-2xl overflow-hidden z-50 animate-in fade-in slide-in-from-top-2 duration-200">
          <div className="p-2 max-h-[60vh] overflow-y-auto scrollbar-hide space-y-1">
            <div className="px-3 py-2 text-xs font-semibold text-gray-500 uppercase tracking-wider">Available Models</div>
            {models.filter(m => !m.disabled).map(model => (
              <ModelItem 
                key={model.id} 
                model={model} 
                isActive={currentModelId === model.id}
                onClick={() => {
                  onSelect(model.id);
                  setIsOpen(false);
                }}
              />
            ))}

            <div className="px-3 py-2 mt-4 text-xs font-semibold text-gray-500 uppercase tracking-wider">Coming Soon</div>
            {models.filter(m => m.disabled).map(model => (
              <ModelItem 
                key={model.id} 
                model={model} 
                isActive={false}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function ModelItem({ model, isActive, onClick }: { model: Model, isActive: boolean, onClick?: () => void }) {
  return (
    <button 
      onClick={onClick}
      disabled={model.disabled}
      className={clsx(
        "w-full text-left p-3 rounded-lg flex items-start gap-3 transition-colors",
        model.disabled ? "opacity-50 cursor-not-allowed grayscale" : "hover:bg-white/5 cursor-pointer",
        isActive ? "bg-primary/10 border border-primary/20" : "border border-transparent"
      )}
    >
      <div className={clsx(
        "mt-0.5 w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0",
        isActive ? "bg-primary text-white" : "bg-white/10 text-gray-400"
      )}>
        {model.disabled ? <Lock className="w-4 h-4" /> : <Sparkles className="w-4 h-4" />}
      </div>
      
      <div className="flex-1 min-w-0">
        <div className="flex items-center justify-between gap-2 mb-1">
          <div className="flex items-center gap-2">
            <span className={clsx("font-medium text-sm", isActive ? "text-primary" : "text-gray-200")}>
              {model.name}
            </span>
            {model.badge && (
              <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-white/10 text-gray-300">
                {model.badge}
              </span>
            )}
          </div>
          {isActive && <Check className="w-4 h-4 text-primary flex-shrink-0" />}
        </div>
        
        <p className="text-xs text-gray-500 mb-3 truncate">
          {model.description}
        </p>

        <div className="flex items-center gap-4 text-xs text-gray-400">
          <Stat icon={<Zap className="w-3 h-3" />} label="Speed" value={model.speed} />
          <Stat icon={<Brain className="w-3 h-3" />} label="Reasoning" value={model.reasoning} />
          <Stat icon={<Code2 className="w-3 h-3" />} label="Coding" value={model.coding} />
        </div>
      </div>
    </button>
  );
}

function Stat({ icon, label, value }: { icon: React.ReactNode, label: string, value: number }) {
  return (
    <div className="flex items-center gap-1.5" title={`${label}: ${value}/100`}>
      {icon}
      <div className="w-12 h-1.5 bg-white/10 rounded-full overflow-hidden">
        <div 
          className={clsx(
            "h-full rounded-full",
            value >= 90 ? "bg-success" : value >= 80 ? "bg-accent" : "bg-warning"
          )}
          style={{ width: `${value}%` }}
        />
      </div>
    </div>
  );
}
