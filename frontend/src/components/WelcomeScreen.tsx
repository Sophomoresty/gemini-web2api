export function WelcomeScreen({ onStart }: { onStart: () => void }) {
  return (
    <div className="flex-1 flex flex-col items-center justify-center p-8 w-full h-full text-center relative overflow-hidden">
      {/* Background decoration */}
      <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[800px] h-[800px] bg-primary/5 rounded-full blur-3xl pointer-events-none"></div>
      
      <div className="relative z-10 max-w-2xl flex flex-col items-center">
        <div className="w-20 h-20 rounded-3xl bg-gradient-to-tr from-primary to-accent flex items-center justify-center mb-8 shadow-[0_0_60px_-15px_rgba(37,99,235,0.5)]">
          <span className="text-4xl font-bold text-white">O</span>
        </div>
        
        <h1 className="text-5xl font-bold text-white mb-4 tracking-tight">
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
          
          <button className="px-8 py-4 bg-white/5 hover:bg-white/10 text-white rounded-xl font-medium border border-white/10 transition-all hover:border-white/20">
            Explore Models
          </button>
        </div>
      </div>
    </div>
  );
}
