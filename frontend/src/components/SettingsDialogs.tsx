import React, { useState, useEffect, useRef } from 'react';
import { User, Mail, Upload, LogOut, Save, Palette, Globe, Clock, Gauge, MessageSquarePlus, Volume2, Bell as BellIcon, Keyboard, Wand2 } from 'lucide-react';
import { clsx } from 'clsx';
import type { Profile, Settings as SettingsType } from '../types';
import { Modal } from './ui/Modal';
import { useApp } from '../store/AppContext';
import { getInitials } from '../lib/utils';

export function ProfileDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { profile, dispatch, notify } = useApp();
  const [draft, setDraft] = useState<Profile>(profile);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => { setDraft(profile); }, [open, profile]);

  const onSave = () => {
    if (!draft.name.trim()) { notify('error', 'Name required'); return; }
    dispatch({ type: 'UPDATE_PROFILE', payload: draft });
    notify('success', 'Profile saved');
    onClose();
  };

  const onAvatarFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (!f) return;
    if (f.size > 2 * 1024 * 1024) { notify('error', 'Avatar too large (< 2MB)'); return; }
    const r = new FileReader();
    r.onload = () => setDraft(d => ({ ...d, avatar: String(r.result || '') }));
    r.readAsDataURL(f);
  };

  return (
    <Modal open={open} onClose={onClose} title="Profile" size="md">
      <div className="p-6 space-y-6">
        <div className="flex items-center gap-5">
          <div className="relative group">
            <div className="w-20 h-20 rounded-2xl bg-gradient-to-tr from-primary to-accent flex items-center justify-center text-white text-2xl font-semibold shadow-lg overflow-hidden">
              {draft.avatar ? <img src={draft.avatar} className="w-full h-full object-cover" alt="" /> : getInitials(draft.name)}
            </div>
            <button
              onClick={() => fileRef.current?.click()}
              className="absolute -bottom-1 -right-1 p-2 rounded-full bg-[var(--card)] border border-[var(--border)] shadow-lg text-primary hover:text-[var(--foreground)] hover:bg-white/10 transition-colors"
              aria-label="Upload avatar"
            >
              <Upload className="w-4 h-4" />
            </button>
            <input ref={fileRef} type="file" accept="image/*" className="hidden" onChange={onAvatarFile} />
          </div>
          <div className="flex-1 min-w-0">
            <div className="text-lg font-semibold text-[var(--foreground)]">{draft.name || 'Your Name'}</div>
            <div className="text-sm text-gray-500 truncate">{draft.email}</div>
          </div>
        </div>

        <div className="space-y-4">
          <Field label="Display name" icon={<User className="w-4 h-4" />}>
            <input
              value={draft.name}
              onChange={e => setDraft(d => ({ ...d, name: e.target.value }))}
              className="w-full bg-transparent outline-none text-[var(--foreground)]"
              placeholder="Your name"
            />
          </Field>

          <Field label="Email" icon={<Mail className="w-4 h-4" />}>
            <input
              type="email"
              value={draft.email}
              onChange={e => setDraft(d => ({ ...d, email: e.target.value }))}
              className="w-full bg-transparent outline-none text-[var(--foreground)]"
              placeholder="you@example.com"
            />
          </Field>
        </div>

        <div className="flex items-center gap-2 pt-2">
          <button onClick={onClose} className="flex-1 px-4 py-2.5 rounded-xl text-sm text-gray-300 bg-white/5 hover:bg-white/10 border border-white/10 transition-colors">
            Cancel
          </button>
          <button onClick={onSave} className="flex-[2] px-4 py-2.5 rounded-xl text-sm bg-primary text-white hover:bg-primary/90 transition-colors flex items-center justify-center gap-2 font-medium shadow-md shadow-primary/20">
            <Save className="w-4 h-4" /> Save changes
          </button>
          <button
            onClick={() => { notify('info', 'Logged out (demo)'); onClose(); }}
            className="p-2.5 rounded-xl text-danger bg-danger/10 hover:bg-danger/20 transition-colors"
            title="Log out"
            aria-label="Log out"
          >
            <LogOut className="w-5 h-5" />
          </button>
        </div>
      </div>
    </Modal>
  );
}

export function SettingsDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { settings, dispatch, notify } = useApp();
  const [draft, setDraft] = useState<SettingsType>(settings);
  const [tab, setTab] = useState<'general' | 'model' | 'notifications' | 'shortcuts'>('general');

  useEffect(() => { setDraft(settings); }, [open, settings]);

  const onSave = () => {
    dispatch({ type: 'UPDATE_SETTINGS', payload: draft });
    notify('success', 'Settings saved');
    onClose();
  };

  const set = <K extends keyof SettingsType>(k: K, v: SettingsType[K]) => setDraft(d => ({ ...d, [k]: v }));
  const setDefaults = <K extends keyof SettingsType['modelDefaults']>(k: K, v: SettingsType['modelDefaults'][K]) =>
    setDraft(d => ({ ...d, modelDefaults: { ...d.modelDefaults, [k]: v } }));

  const tabs = [
    { id: 'general' as const, label: 'General', icon: <Palette className="w-4 h-4" /> },
    { id: 'model' as const, label: 'Model', icon: <Wand2 className="w-4 h-4" /> },
    { id: 'notifications' as const, label: 'Alerts', icon: <BellIcon className="w-4 h-4" /> },
    { id: 'shortcuts' as const, label: 'Shortcuts', icon: <Keyboard className="w-4 h-4" /> },
  ];

  return (
    <Modal open={open} onClose={onClose} title="Settings" size="xl">
      <div className="flex min-h-[500px]">
        <div className="w-48 shrink-0 border-r border-[var(--border)] p-3 space-y-1">
          {tabs.map(t => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={clsx(
                'w-full flex items-center gap-2 px-3 py-2 rounded-lg text-sm text-left transition-colors',
                tab === t.id ? 'bg-primary/10 text-primary font-medium' : 'text-gray-400 hover:text-[var(--foreground)] hover:bg-white/5'
              )}
            >
              {t.icon}
              {t.label}
            </button>
          ))}
        </div>
        <div className="flex-1 overflow-y-auto p-6 space-y-5">
          {tab === 'general' && (
            <>
              <div>
                <h3 className="text-sm font-semibold text-[var(--foreground)] mb-3 flex items-center gap-2"><Palette className="w-4 h-4 text-primary" /> Appearance</h3>
                <div className="space-y-3">
                  <div>
                    <div className="text-xs font-medium text-gray-400 mb-2">Theme</div>
                    <div className="grid grid-cols-3 gap-2">
                      {([
                        { id: 'light' as const, label: 'Light', icon: <SunIcon /> },
                        { id: 'dark' as const, label: 'Dark', icon: <MoonIcon /> },
                        { id: 'system' as const, label: 'System', icon: <MonitorIcon /> },
                      ]).map(opt => (
                        <button
                          key={opt.id}
                          onClick={() => set('theme', opt.id)}
                          className={clsx(
                            'p-3 rounded-xl border transition-all flex flex-col items-center gap-2',
                            draft.theme === opt.id
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
                  <Toggle label="Enable animations" hint="Smooth transitions and effects" value={draft.animations} onChange={v => set('animations', v)} />
                  <Toggle label="Sidebar collapsed by default" value={draft.sidebarCollapsed} onChange={v => set('sidebarCollapsed', v)} />
                </div>
              </div>

              <div>
                <h3 className="text-sm font-semibold text-[var(--foreground)] mb-3 flex items-center gap-2"><Globe className="w-4 h-4 text-primary" /> Localization</h3>
                <div className="space-y-3">
                  <Select label="Language" value={draft.language} onChange={v => set('language', v)} options={[
                    { v: 'en', l: 'English' }, { v: 'zh', l: '中文' }, { v: 'es', l: 'Español' }, { v: 'fr', l: 'Français' }, { v: 'de', l: 'Deutsch' }, { v: 'ja', l: '日本語' },
                  ]} />
                  <Select label="Timezone" value={draft.timezone} onChange={v => set('timezone', v)} options={[
                    'UTC', 'America/New_York', 'America/Los_Angeles', 'Europe/London', 'Europe/Berlin', 'Asia/Tokyo', 'Asia/Shanghai', 'Asia/Kolkata', 'Australia/Sydney',
                  ].map(tz => ({ v: tz, l: tz.replace(/_/g, ' ') }))}
                    icon={<Clock className="w-4 h-4" />} />
                </div>
              </div>
            </>
          )}

          {tab === 'model' && (
            <>
              <div>
                <h3 className="text-sm font-semibold text-[var(--foreground)] mb-3 flex items-center gap-2"><Wand2 className="w-4 h-4 text-primary" /> Default Model</h3>
                <Select
                  label="Default model"
                  value={draft.modelDefaults.model}
                  onChange={v => setDefaults('model', v)}
                  icon={<MessageSquarePlus className="w-4 h-4" />}
                  options={[
                    { v: 'gemini-3.6-flash', l: 'Gemini 3.6 Flash' },
                    { v: 'gemini-3.5-flash', l: 'Gemini 3.5 Flash' },
                    { v: 'gemini-3.5-flash-thinking', l: 'Gemini Flash Thinking' },
                    { v: 'gemini-flash-lite', l: 'Gemini Flash Lite' },
                    { v: 'gemini-auto', l: 'Gemini Auto' },
                  ]}
                />
              </div>
              <div>
                <h3 className="text-sm font-semibold text-[var(--foreground)] mb-3 flex items-center gap-2"><Gauge className="w-4 h-4 text-primary" /> Generation Parameters</h3>
                <div className="space-y-4">
                  <Slider label="Temperature" hint="Higher = more creative" min={0} max={2} step={0.05} value={draft.modelDefaults.temperature} onChange={v => setDefaults('temperature', v)} />
                  <Slider label="Top P" hint="Nucleus sampling threshold" min={0} max={1} step={0.01} value={draft.modelDefaults.topP} onChange={v => setDefaults('topP', v)} />
                  <Slider label="Max tokens" hint="Response length limit" min={256} max={32768} step={256} value={draft.modelDefaults.maxTokens} onChange={v => setDefaults('maxTokens', v)} intOnly />
                  <Toggle label="Stream responses" hint="Receive tokens as they are generated" value={draft.streaming} onChange={v => set('streaming', v)} />
                </div>
              </div>
            </>
          )}

          {tab === 'notifications' && (
            <>
              <div>
                <h3 className="text-sm font-semibold text-[var(--foreground)] mb-3 flex items-center gap-2"><BellIcon className="w-4 h-4 text-primary" /> Notifications</h3>
                <div className="space-y-3">
                  <Toggle label="Enable notifications" hint="Show toast popups for events" value={draft.notifications.enabled} onChange={v => set('notifications', { ...draft.notifications, enabled: v })} />
                  <Toggle label="Sound alerts" hint="Play a sound on new messages" value={draft.notifications.sound} onChange={v => set('notifications', { ...draft.notifications, sound: v })} disabled={!draft.notifications.enabled} icon={<Volume2 className="w-4 h-4" />} />
                  <Toggle label="Desktop notifications" hint="Request browser permission for native notifications" value={draft.notifications.desktop} onChange={(v) => {
                    if (v && 'Notification' in window) { Notification.requestPermission().then(p => notify('info', p === 'granted' ? 'Notifications enabled' : 'Permission denied')); }
                    set('notifications', { ...draft.notifications, desktop: v });
                  }} disabled={!draft.notifications.enabled} />
                </div>
              </div>
            </>
          )}

          {tab === 'shortcuts' && (
            <>
              <div>
                <h3 className="text-sm font-semibold text-[var(--foreground)] mb-3 flex items-center gap-2"><Keyboard className="w-4 h-4 text-primary" /> Keyboard Shortcuts</h3>
                <div className="space-y-2">
                  {[
                    { k: 'newChat', label: 'New Chat', hint: 'Start a new conversation' },
                    { k: 'search', label: 'Search Chats', hint: 'Open search dialog' },
                    { k: 'toggleSidebar', label: 'Toggle Sidebar', hint: 'Collapse or expand' },
                    { k: 'closeDialog', label: 'Close Dialogs', hint: 'Dismiss any open modal' },
                  ].map(s => (
                    <div key={s.k} className="flex items-center justify-between p-3 rounded-xl bg-[var(--surface)] border border-[var(--border)]">
                      <div>
                        <div className="text-sm font-medium text-[var(--foreground)]">{s.label}</div>
                        <div className="text-[11px] text-gray-500">{s.hint}</div>
                      </div>
                      <div className="flex gap-1">
                        {draft.shortcuts[s.k as keyof typeof draft.shortcuts].split('+').map((p, i) => (
                          <React.Fragment key={i}>
                            {i > 0 && <span className="text-gray-500 text-xs self-center mx-0.5">+</span>}
                            <kbd className="px-2 py-1 rounded-md bg-white/5 border border-white/10 font-mono text-[11px] text-gray-300">{p}</kbd>
                          </React.Fragment>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </>
          )}
        </div>
      </div>
      <div className="px-6 py-4 border-t border-[var(--border)] flex gap-2 justify-end">
        <button onClick={onClose} className="px-4 py-2 rounded-xl text-sm text-gray-300 bg-white/5 hover:bg-white/10 border border-white/10 transition-colors">Cancel</button>
        <button onClick={onSave} className="px-5 py-2 rounded-xl text-sm bg-primary text-white hover:bg-primary/90 transition-colors font-medium shadow-md shadow-primary/20 flex items-center gap-2">
          <Save className="w-4 h-4" /> Save
        </button>
      </div>
    </Modal>
  );
}

function Field({ label, icon, children }: { label: string; icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <label className="block">
      <div className="text-xs font-medium text-gray-400 mb-1.5 flex items-center gap-1.5">{icon}<span>{label}</span></div>
      <div className="p-3 rounded-xl bg-[var(--surface)] border border-[var(--border)] focus-within:ring-2 focus-within:ring-primary/30 focus-within:border-primary/50 transition-all">
        {children}
      </div>
    </label>
  );
}

function Toggle({ label, hint, value, onChange, disabled, icon }: { label: string; hint?: string; value: boolean; onChange: (v: boolean) => void; disabled?: boolean; icon?: React.ReactNode }) {
  return (
    <div className={clsx('flex items-center justify-between p-3 rounded-xl bg-[var(--surface)] border border-[var(--border)]', disabled && 'opacity-50 pointer-events-none')}>
      <div className="flex items-center gap-3 min-w-0 flex-1 pr-4">
        {icon && <div className="text-gray-400 flex-shrink-0">{icon}</div>}
        <div className="min-w-0">
          <div className="text-sm font-medium text-[var(--foreground)]">{label}</div>
          {hint && <div className="text-[11px] text-gray-500 mt-0.5">{hint}</div>}
        </div>
      </div>
      <button
        onClick={() => onChange(!value)}
        className={clsx(
          'relative w-11 h-6 rounded-full transition-colors flex-shrink-0',
          value ? 'bg-primary' : 'bg-white/10'
        )}
        role="switch"
        aria-checked={value}
      >
        <span className={clsx(
          'absolute top-1 w-4 h-4 rounded-full bg-white shadow-md transition-transform',
          value ? 'translate-x-6' : 'translate-x-1'
        )} />
      </button>
    </div>
  );
}

function Select({ label, value, onChange, options, icon }: { label: string; value: string; onChange: (v: string) => void; options: Array<{ v: string; l: string }>; icon?: React.ReactNode }) {
  return (
    <label className="block">
      <div className="text-xs font-medium text-gray-400 mb-1.5 flex items-center gap-1.5">{icon}<span>{label}</span></div>
      <select
        value={value}
        onChange={e => onChange(e.target.value)}
        className="w-full p-3 rounded-xl bg-[var(--surface)] border border-[var(--border)] text-[var(--foreground)] outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary/50 transition-all"
      >
        {options.map(o => <option key={o.v} value={o.v} className="bg-[var(--card)]">{o.l}</option>)}
      </select>
    </label>
  );
}

function Slider({ label, hint, min, max, step, value, onChange, intOnly }: { label: string; hint?: string; min: number; max: number; step: number; value: number; onChange: (v: number) => void; intOnly?: boolean }) {
  return (
    <div className="p-3 rounded-xl bg-[var(--surface)] border border-[var(--border)] space-y-2">
      <div className="flex items-center justify-between">
        <div>
          <div className="text-sm font-medium text-[var(--foreground)]">{label}</div>
          {hint && <div className="text-[11px] text-gray-500">{hint}</div>}
        </div>
        <div className="font-mono text-sm text-primary bg-primary/10 px-2 py-0.5 rounded-md min-w-[3.5rem] text-center">
          {intOnly ? Math.round(value) : value.toFixed(2)}
        </div>
      </div>
      <input
        type="range"
        min={min} max={max} step={step} value={value}
        onChange={e => onChange(Number(e.target.value))}
        className="w-full accent-primary"
      />
      <div className="flex justify-between text-[10px] text-gray-500">
        <span>{intOnly ? min : min.toFixed(1)}</span>
        <span>{intOnly ? max : max.toFixed(1)}</span>
      </div>
    </div>
  );
}

function SunIcon() { return <svg className="w-6 h-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" /></svg>; }
function MoonIcon() { return <svg className="w-6 h-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" /></svg>; }
function MonitorIcon() { return <svg className="w-6 h-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="2" y="3" width="20" height="14" rx="2" /><path d="M8 21h8M12 17v4" /></svg>; }
