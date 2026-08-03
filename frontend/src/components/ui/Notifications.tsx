import React, { useEffect, useState } from 'react';
import { CheckCircle, XCircle, AlertCircle, Info, X } from 'lucide-react';
import { useApp } from '../../store/AppContext';
import type { NotificationItem } from '../../types';
import { cn } from '../../lib/utils';

const icons: Record<NotificationItem['type'], React.ReactElement> = {
  success: <CheckCircle className="w-5 h-5 text-success" />,
  error: <XCircle className="w-5 h-5 text-danger" />,
  warning: <AlertCircle className="w-5 h-5 text-warning" />,
  info: <Info className="w-5 h-5 text-primary" />,
};

const borderColors: Record<NotificationItem['type'], string> = {
  success: 'border-l-success',
  error: 'border-l-danger',
  warning: 'border-l-warning',
  info: 'border-l-primary',
};

export function Toaster() {
  const { notifications, dispatch } = useApp();
  const [toasts, setToasts] = useState<NotificationItem[]>([]);

  useEffect(() => {
    const unread = notifications.filter(n => !n.read).slice(0, 5);
    setToasts(unread);

    const timers = unread.map(n =>
      setTimeout(() => {
        dispatch({ type: 'MARK_NOTIFICATION_READ', payload: n.id });
      }, 4000)
    );

    return () => {
      timers.forEach(clearTimeout);
    };
  }, [notifications, dispatch]);

  return (
    <div className="fixed bottom-4 right-4 z-[60] flex flex-col gap-2 pointer-events-none">
      {toasts.map(toast => (
        <Toast key={toast.id} toast={toast} onClose={() => dispatch({ type: 'MARK_NOTIFICATION_READ', payload: toast.id })} />
      ))}
    </div>
  );
}

function Toast({ toast, onClose }: { toast: NotificationItem; onClose: () => void }) {
  const [leaving, setLeaving] = useState(false);

  useEffect(() => {
    const t = setTimeout(() => setLeaving(true), 3800);
    return () => clearTimeout(t);
  }, []);

  return (
    <div
      className={cn(
        'pointer-events-auto min-w-[300px] max-w-sm bg-[var(--card)] border border-[var(--border)] rounded-xl shadow-xl border-l-4 p-4 pr-10 relative',
        borderColors[toast.type],
        leaving ? 'animate-out fade-out slide-out-to-right duration-300' : 'animate-in slide-in-from-right duration-300'
      )}
      role="alert"
    >
      <div className="flex items-start gap-3">
        <div className="mt-0.5 flex-shrink-0">{icons[toast.type]}</div>
        <div className="min-w-0">
          <div className="font-medium text-sm text-[var(--foreground)]">{toast.title}</div>
          {toast.message && <div className="mt-0.5 text-xs text-gray-400">{toast.message}</div>}
        </div>
      </div>
      <button
        onClick={onClose}
        className="absolute top-2 right-2 p-1 rounded text-gray-400 hover:text-[var(--foreground)] hover:bg-white/10 transition-colors"
        aria-label="Dismiss"
      >
        <X className="w-3.5 h-3.5" />
      </button>
    </div>
  );
}

interface DropdownItem {
  icon?: React.ReactNode;
  label: string;
  onClick: () => void;
  danger?: boolean;
  disabled?: boolean;
}

export function ContextMenu({ items, onClose }: { items: DropdownItem[]; onClose: () => void }) {
  return (
    <>
      <div className="fixed inset-0 z-40" onClick={onClose} />
      <div className="fixed z-50 min-w-[180px] bg-[var(--card)] border border-[var(--border)] rounded-lg shadow-2xl py-1.5 animate-in fade-in zoom-in-95 duration-100">
        {items.map((item, i) => (
          <button
            key={i}
            onClick={() => { item.onClick(); onClose(); }}
            disabled={item.disabled}
            className={cn(
              'w-full flex items-center gap-2 px-3 py-1.5 text-sm text-left transition-colors',
              item.danger ? 'text-danger hover:bg-danger/10' : 'text-gray-200 hover:bg-white/10',
              item.disabled && 'opacity-50 cursor-not-allowed'
            )}
          >
            {item.icon}
            {item.label}
          </button>
        ))}
      </div>
    </>
  );
}
