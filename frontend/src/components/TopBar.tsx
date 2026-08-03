import React, { useState, useMemo } from 'react';
import { Share2, Sun, Moon, Monitor, Bell, Menu, X, Copy, Link as LinkIcon, Check, Clock, Wand2, Sparkles } from 'lucide-react';
import { clsx } from 'clsx';
import { ModelSelector } from './ModelSelector';
import { useApp } from '../store/AppContext';
import { Modal } from './ui/Modal';
import { copyToClipboard, formatTimestamp, getInitials } from '../lib/utils';

export function TopBar() {
  const {
    currentChatId, conversations, selectedModel, dispatch,
    notify, openDialog, closeDialogs, dialog, settings, profile, notifications,
  } = useApp();

  const currentChat = conversations.find(c => c.id === currentChatId);
  const unread = notifications.filter(n => !n.read).length;

  const cycleTheme = () => {
    const order: Array<'system' | 'dark' | 'light'> = ['system', 'dark', 'light'];
    const cur = order.indexOf(settings.theme);
    const next = order[(cur + 1) % order.length];
    dispatch({ type: 'UPDATE_SETTINGS', payload: { theme: next } });
    notify('info', `Theme: ${next.charAt(0).toUpperCase() + next.slice(1)}`);
  };

  const themeIcon = settings.theme === 'dark'
    ? <Moon className="w-4 h-4" />
    : settings.theme === 'light'
      ? <Sun className="w-4 h-4" />
      : <Monitor className="w-4 h-4" />;

  return (
    <>
      <header className="h-14 flex items-center justify-between px-2 sm:px-4 border-b border-[var(--border)] bg-[var(--background)]/80 backdrop-blur-md sticky top-0 z-10 shrink-0">
        <div className="flex items-center gap-2 min-w-0">
          <button
            onClick={() => dispatch({ type: 'TOGGLE_MOBILE_SIDEBAR', payload: true })}
            className="md:hidden p-2 text-gray-400 hover:text-[var(--foreground)] hover:bg-white/5 rounded-lg transition-colors"
            aria-label="Open sidebar"
          >
            <Menu className="w-5 h-5" />
          </button>

          <ModelSelector currentModelId={selectedModel} onSelect={(id) => { dispatch({ type: 'SELECT_MODEL', payload: id }); notify('success', 'Model switched', `Using ${id}`); }} />

          <div className="hidden sm:block h-4 w-px bg-[var(--border)]"></div>

          <div className="hidden sm:flex items-center gap-2 min-w-0 max-w-[40%]">
            <span className="text-sm font-medium text-[var(--foreground)] truncate">
              {currentChat?.title || 'New Chat'}
            </span>
            {currentChat && (
              <span className="text-[10px] text-gray-500 flex-shrink-0" title={`Updated ${formatTimestamp(currentChat.updatedAt)}`}>
                {currentChat.messages.length} messages
              </span>
            )}
          </div>
        </div>

        <div className="flex items-center gap-0.5 sm:gap-1">
          <IconButton
            onClick={() => openDialog({ share: true })}
            tooltip="Share conversation"
            icon={<Share2 className="w-4 h-4" />}
          />
          <IconButton
            onClick={cycleTheme}
            tooltip={`Theme: ${settings.theme}`}
            icon={themeIcon}
          />
          <div className="relative">
            <IconButton
              onClick={() => openDialog({ notifications: true })}
              tooltip="Notifications"
              icon={
                <div className="relative">
                  <Bell className="w-4 h-4" />
                  {unread > 0 && (
                    <span className="absolute -top-1 -right-1 w-4 h-4 rounded-full bg-danger text-white text-[10px] font-bold flex items-center justify-center animate-pulse">
                      {Math.min(unread, 9)}
                    </span>
                  )}
                </div>
              }
            />
          </div>
          <button
            onClick={() => openDialog({ profile: true })}
            className="ml-1 sm:ml-2 w-8 h-8 rounded-full bg-gradient-to-tr from-primary to-accent flex items-center justify-center text-white font-medium text-xs cursor-pointer hover:opacity-90 transition-opacity shadow-sm"
            aria-label="Profile menu"
            title={profile.name}
          >
            {profile.avatar ? (
              <img src={profile.avatar} alt="" className="w-full h-full rounded-full object-cover" />
            ) : getInitials(profile.name)}
          </button>
        </div>
      </header>

      <ShareDialog open={dialog.share} onClose={closeDialogs} chatTitle={currentChat?.title || ''} chatMessagesCount={currentChat?.messages.length || 0} />
      <NotificationsDialog open={dialog.notifications} onClose={closeDialogs} />
    </>
  );
}

function IconButton({ icon, tooltip, onClick }: { icon: React.ReactNode; tooltip: string; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="p-2 text-gray-400 hover:text-[var(--foreground)] hover:bg-white/5 rounded-lg transition-colors"
      title={tooltip}
      aria-label={tooltip}
    >
      {icon}
    </button>
  );
}

function ShareDialog({ open, onClose, chatTitle, chatMessagesCount }: { open: boolean; onClose: () => void; chatTitle: string; chatMessagesCount: number }) {
  const { notify, profile } = useApp();
  const [copied, setCopied] = useState(false);
  const [linkType, setLinkType] = useState<'share' | 'markdown' | 'json'>('share');

  const onCopyLink = async () => {
    const url = `${window.location.origin}${window.location.pathname}`;
    await copyToClipboard(url);
    setCopied(true);
    notify('success', 'Copied share link');
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <Modal open={open} onClose={onClose} title="Share conversation" size="md">
      <div className="p-6 space-y-5">
        <div className="p-4 rounded-xl bg-[var(--surface)] border border-[var(--border)]">
          <div className="flex items-center gap-3">
            <div className="w-12 h-12 rounded-xl bg-primary/20 flex items-center justify-center text-primary">
              <LinkIcon className="w-6 h-6" />
            </div>
            <div className="min-w-0 flex-1">
              <div className="font-semibold text-[var(--foreground)] truncate">{chatTitle || 'Untitled'}</div>
              <div className="text-xs text-gray-500 mt-0.5">{chatMessagesCount} messages · Shared by {profile.name}</div>
            </div>
          </div>
        </div>

        <div>
          <label className="block text-xs font-medium text-gray-400 mb-2">Export options</label>
          <div className="grid grid-cols-3 gap-2">
            {[
              { id: 'share' as const, label: 'Copy URL', icon: <LinkIcon className="w-4 h-4" /> },
              { id: 'markdown' as const, label: 'Markdown', icon: <Wand2 className="w-4 h-4" /> },
              { id: 'json' as const, label: 'JSON', icon: <Sparkles className="w-4 h-4" /> },
            ].map(opt => (
              <button
                key={opt.id}
                onClick={() => setLinkType(opt.id)}
                className={clsx(
                  'p-3 rounded-xl border transition-all flex flex-col items-center gap-1.5 text-center',
                  linkType === opt.id
                    ? 'border-primary/50 bg-primary/5 text-primary'
                    : 'border-[var(--border)] text-gray-400 hover:text-[var(--foreground)] hover:bg-white/5'
                )}
              >
                {opt.icon}
                <span className="text-xs font-medium">{opt.label}</span>
              </button>
            ))}
          </div>
        </div>

        <div>
          <label className="block text-xs font-medium text-gray-400 mb-2">Share access</label>
          <div className="p-3 rounded-xl bg-[var(--surface)] border border-[var(--border)] flex items-center gap-3">
            <div className="w-9 h-9 rounded-lg bg-white/5 flex items-center justify-center text-gray-400">
              <LinkIcon className="w-4 h-4" />
            </div>
            <div className="flex-1 min-w-0">
              <div className="text-sm font-medium text-[var(--foreground)]">Anyone with link can view</div>
              <div className="text-xs text-gray-500">Shareable (read-only) link</div>
            </div>
            <span className="text-xs px-2 py-0.5 rounded-full bg-success/10 text-success font-medium">Public</span>
          </div>
        </div>

        <div className="flex gap-2 pt-2">
          <button
            onClick={onClose}
            className="flex-1 px-4 py-2.5 rounded-xl text-sm text-gray-300 bg-white/5 hover:bg-white/10 border border-white/10 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={onCopyLink}
            className="flex-[2] px-4 py-2.5 rounded-xl text-sm bg-primary text-white hover:bg-primary/90 transition-colors flex items-center justify-center gap-2 font-medium shadow-md shadow-primary/20"
          >
            {copied ? <Check className="w-4 h-4" /> : <Copy className="w-4 h-4" />}
            {copied ? 'Copied!' : (linkType === 'share' ? 'Copy link' : `Export as ${linkType}`)}
          </button>
        </div>
      </div>
    </Modal>
  );
}

function NotificationsDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { notifications, dispatch } = useApp();
  const items = useMemo(() => notifications.slice(0, 30), [notifications]);

  const icons = {
    success: <Check className="w-4 h-4 text-success" />,
    error: <X className="w-4 h-4 text-danger" />,
    warning: <Clock className="w-4 h-4 text-warning" />,
    info: <Bell className="w-4 h-4 text-primary" />,
  };

  const dotColors = {
    success: 'bg-success',
    error: 'bg-danger',
    warning: 'bg-warning',
    info: 'bg-primary',
  };

  return (
    <Modal open={open} onClose={onClose} title="Notifications" size="md">
      <div>
        {items.length === 0 ? (
          <div className="p-10 text-center">
            <Bell className="w-10 h-10 mx-auto mb-3 text-gray-500 opacity-50" />
            <p className="text-sm text-gray-400">No notifications yet</p>
          </div>
        ) : (
          <>
            <div className="flex items-center justify-between px-6 py-2 border-b border-[var(--border)]">
              <span className="text-xs font-medium text-gray-400">{items.filter(n => !n.read).length} unread</span>
              <button
                onClick={() => { dispatch({ type: 'CLEAR_NOTIFICATIONS' }); }}
                className="text-xs text-primary hover:text-primary/80 font-medium"
              >
                Clear all
              </button>
            </div>
            <div className="max-h-[60vh] overflow-y-auto scrollbar-hide">
              {items.map(n => (
                <div
                  key={n.id}
                  className={clsx(
                    'flex gap-3 px-6 py-3.5 border-b border-[var(--border)] last:border-b-0 transition-colors cursor-pointer hover:bg-white/5',
                    !n.read && 'bg-primary/[0.02]'
                  )}
                  onClick={() => dispatch({ type: 'MARK_NOTIFICATION_READ', payload: n.id })}
                >
                  <div className="flex flex-col items-center pt-0.5">
                    <div className={clsx('w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0', n.read ? 'bg-white/5' : `${dotColors[n.type]}/15`)}>
                      {icons[n.type]}
                    </div>
                    {!n.read && <div className={clsx('w-1.5 h-1.5 rounded-full mt-1.5', dotColors[n.type])} />}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-start justify-between gap-2">
                      <div className="text-sm font-medium text-[var(--foreground)]">{n.title}</div>
                      <div className="text-[10px] text-gray-500 flex-shrink-0 whitespace-nowrap">{formatTimestamp(n.timestamp)}</div>
                    </div>
                    {n.message && <div className="text-xs text-gray-400 mt-0.5 leading-relaxed">{n.message}</div>}
                  </div>
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    </Modal>
  );
}
