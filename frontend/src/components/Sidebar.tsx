import React, { useState, useMemo, useRef, useEffect } from 'react';
import {
  MessageSquarePlus, Search, Clock, Pin, Folder, FolderPlus, Settings, User,
  PanelLeftClose, PanelLeftOpen, ChevronDown, ChevronRight, Trash2, Edit2,
  MoreHorizontal, X, Filter, Star, Menu
} from 'lucide-react';
import { clsx } from 'clsx';
import type { Conversation, Folder as FolderType } from '../types';
import { generateId, formatTimestamp, getInitials, highlightText, truncate } from '../lib/utils';
import { useApp } from '../store/AppContext';
import { Modal, ConfirmDialog } from './ui/Modal';

export function Sidebar() {
  const {
    conversations, folders, pinnedIds, currentChatId, sidebarCollapsed,
    sidebarMobileOpen, profile, selectedModel,
    dispatch, createNewChat, selectChat, deleteChat, pinChat, notify, openDialog, closeDialogs, dialog
  } = useApp();

  const [search] = useState('');
  const [folderMenu, setFolderMenu] = useState<string | null>(null);
  const [chatMenu, setChatMenu] = useState<{ id: string; x: number; y: number } | null>(null);
  const [draggedChat, setDraggedChat] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState<string | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (dialog.search) searchRef.current?.focus();
  }, [dialog.search]);

  const sortedPinned = useMemo(() =>
    pinnedIds.map(id => conversations.find(c => c.id === id)).filter(Boolean) as Conversation[],
    [pinnedIds, conversations]
  );

  const sortedRecent = useMemo(() => {
    const recent = conversations
      .filter(c => !pinnedIds.includes(c.id))
      .sort((a, b) => b.updatedAt - a.updatedAt);
    if (!search.trim()) return recent.slice(0, 30);
    const q = search.toLowerCase();
    return recent.filter(c =>
      c.title.toLowerCase().includes(q) ||
      c.messages.some(m => m.content.toLowerCase().includes(q)) ||
      (c.tags || []).some(t => t.toLowerCase().includes(q))
    ).slice(0, 50);
  }, [conversations, pinnedIds, search]);

  const chatsByFolder = useMemo(() => {
    const map = new Map<string | null, Conversation[]>();
    for (const f of folders) map.set(f.id, []);
    map.set(null, []);
    for (const c of conversations) {
      if (pinnedIds.includes(c.id)) continue;
      const bucket = c.folderId || null;
      if (map.has(bucket)) map.get(bucket)!.push(c);
    }
    return map;
  }, [conversations, folders, pinnedIds]);

  const isMobile = typeof window !== 'undefined' && window.matchMedia('(max-width: 768px)').matches;

  const handleNewChat = () => {
    createNewChat(selectedModel);
    if (isMobile) dispatch({ type: 'TOGGLE_MOBILE_SIDEBAR', payload: false });
    notify('success', 'New chat created');
  };

  const onToggleFolder = (id: string) => {
    const folder = folders.find(f => f.id === id);
    if (folder) dispatch({ type: 'UPDATE_FOLDER', payload: { id, updates: { expanded: !folder.expanded } } });
  };

  const onDeleteFolder = (id: string) => {
    dispatch({ type: 'DELETE_FOLDER', payload: id });
    notify('info', 'Folder deleted');
    setFolderMenu(null);
  };

  const onRenameFolder = (id: string, name: string) => {
    dispatch({ type: 'UPDATE_FOLDER', payload: { id, updates: { name } } });
    notify('success', 'Folder renamed');
    setFolderMenu(null);
  };

  const addFolder = (name: string) => {
    const colors = ['#2563EB', '#22C55E', '#F59E0B', '#EF4444', '#8B5CF6', '#EC4899', '#06B6D4'];
    dispatch({
      type: 'ADD_FOLDER',
      payload: { id: generateId(), name, color: colors[Math.floor(Math.random() * colors.length)], expanded: true, createdAt: Date.now() }
    });
    notify('success', `Folder "${name}" created`);
    closeDialogs();
  };

  const onChatDropToFolder = (chatId: string, folderId: string | null) => {
    dispatch({ type: 'MOVE_TO_FOLDER', payload: { chatId, folderId } });
    notify('info', folderId ? 'Chat moved to folder' : 'Chat removed from folder');
    setDragOver(null);
    setDraggedChat(null);
  };

  const sidebarContent = (
    <>
      <div className="h-14 flex items-center px-4 pt-2 justify-between shrink-0">
        <div className="flex items-center gap-2 font-bold text-lg text-[var(--foreground)]">
          <div className="w-7 h-7 rounded-lg bg-primary flex items-center justify-center text-white text-sm shadow-md">
            O
          </div>
          {!sidebarCollapsed && <span>Omni<span className="text-primary">AI</span></span>}
        </div>
        {!sidebarCollapsed && (
          <button
            onClick={() => dispatch({ type: 'TOGGLE_SIDEBAR' })}
            className="md:hidden p-1.5 rounded-lg text-gray-400 hover:text-[var(--foreground)] hover:bg-white/10"
            aria-label="Close sidebar"
          >
            <X className="w-5 h-5" />
          </button>
        )}
      </div>

      <div className="px-3 py-2 shrink-0">
        <button
          onClick={handleNewChat}
          className="w-full flex items-center gap-2 px-3 py-2.5 bg-primary hover:bg-primary/90 text-white rounded-xl text-sm font-medium transition-all shadow-md shadow-primary/20 hover:shadow-primary/40"
          aria-label="New chat"
        >
          <MessageSquarePlus className="w-4 h-4 flex-shrink-0" />
          {!sidebarCollapsed && <span>New Chat</span>}
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-3 space-y-5 scrollbar-hide min-h-0">
        <div className="shrink-0">
          {sidebarCollapsed ? (
            <button
              onClick={() => openDialog({ search: true })}
              className="w-10 h-10 flex items-center justify-center rounded-lg text-gray-400 hover:text-[var(--foreground)] hover:bg-white/10"
              aria-label="Search"
            >
              <Search className="w-4 h-4" />
            </button>
          ) : (
            <button
              onClick={() => openDialog({ search: true })}
              className="w-full flex items-center gap-2 px-3 py-2 text-sm text-gray-400 hover:text-[var(--foreground)] hover:bg-white/5 rounded-lg transition-colors border border-[var(--border)]"
            >
              <Search className="w-4 h-4 flex-shrink-0" />
              <span className="flex-1 text-left">Search Chats</span>
              <kbd className="px-1.5 py-0.5 rounded bg-white/5 border border-white/10 font-mono text-[10px]">Ctrl+K</kbd>
            </button>
          )}
        </div>

        {sortedPinned.length > 0 && !search.trim() && (
          <div className="shrink-0">
            <SectionTitle icon={<Pin className="w-3 h-3" />} label="Pinned" />
            <div className="space-y-0.5">
              {sortedPinned.map(c => (
                <ChatNavItem
                  key={c.id}
                  conversation={c}
                  active={c.id === currentChatId}
                  onClick={() => { selectChat(c.id); if (isMobile) dispatch({ type: 'TOGGLE_MOBILE_SIDEBAR', payload: false }); }}
                  query={search}
                  pinned
                  onMenu={(x, y) => setChatMenu({ id: c.id, x, y })}
                  onPinToggle={() => { pinChat(c.id); notify('info', 'Unpinned'); }}
                  onDragStart={() => setDraggedChat(c.id)}
                  onDragEnd={() => { setDraggedChat(null); setDragOver(null); }}
                />
              ))}
            </div>
          </div>
        )}

        {!search.trim() && folders.length > 0 && (
          <div className="shrink-0">
            <div className="flex items-center justify-between px-3 mb-1.5">
              <span className="text-xs font-medium text-gray-500 uppercase tracking-wider flex items-center gap-1.5">
                <Folder className="w-3 h-3" /> Folders
              </span>
              <button
                onClick={() => openDialog({ folderCreate: true })}
                className="p-1 text-gray-500 hover:text-[var(--foreground)] hover:bg-white/10 rounded transition-colors"
                aria-label="Create folder"
              >
                <FolderPlus className="w-3.5 h-3.5" />
              </button>
            </div>
            <div className="space-y-0.5">
              {folders.map(folder => (
                <div key={folder.id}
                  onDragOver={(e) => { if (draggedChat && draggedChat !== folder.id) { e.preventDefault(); setDragOver(folder.id); } }}
                  onDragLeave={() => dragOver === folder.id && setDragOver(null)}
                  onDrop={() => draggedChat && onChatDropToFolder(draggedChat, folder.id)}
                  className={clsx('rounded-lg transition-colors', dragOver === folder.id && 'bg-primary/10 ring-1 ring-primary/30')}
                >
                  <FolderNavItem
                    folder={folder}
                    collapsed={sidebarCollapsed}
                    onToggle={() => onToggleFolder(folder.id)}
                    onMenu={() => setFolderMenu(folder.id)}
                  />
                  {folder.expanded && !sidebarCollapsed && (
                    <div className="ml-6 space-y-0.5 mt-0.5">
                      {(chatsByFolder.get(folder.id) || []).slice(0, 20).map(c => (
                        <ChatNavItem
                          key={c.id}
                          conversation={c}
                          active={c.id === currentChatId}
                          onClick={() => { selectChat(c.id); if (isMobile) dispatch({ type: 'TOGGLE_MOBILE_SIDEBAR', payload: false }); }}
                          query=""
                          small
                          onMenu={(x, y) => setChatMenu({ id: c.id, x, y })}
                          onPinToggle={() => { pinChat(c.id); notify('info', pinnedIds.includes(c.id) ? 'Unpinned' : 'Pinned'); }}
                          onDragStart={() => setDraggedChat(c.id)}
                          onDragEnd={() => { setDraggedChat(null); setDragOver(null); }}
                        />
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="min-h-0 flex-1">
          <SectionTitle icon={<Clock className="w-3 h-3" />} label={search.trim() ? 'Results' : 'Recent'} />
          <div
            className="space-y-0.5"
            onDragOver={(e) => { if (draggedChat) { e.preventDefault(); setDragOver('__root__'); } }}
            onDragLeave={() => dragOver === '__root__' && setDragOver(null)}
            onDrop={() => draggedChat && onChatDropToFolder(draggedChat, null)}
          >
            {sortedRecent.length === 0 ? (
              <div className="px-3 py-4 text-xs text-gray-500 text-center">
                {search.trim() ? 'No matches' : 'No chats yet'}
              </div>
            ) : sortedRecent.map(c => (
              <ChatNavItem
                key={c.id}
                conversation={c}
                active={c.id === currentChatId}
                onClick={() => { selectChat(c.id); if (isMobile) dispatch({ type: 'TOGGLE_MOBILE_SIDEBAR', payload: false }); }}
                query={search}
                onMenu={(x, y) => setChatMenu({ id: c.id, x, y })}
                onPinToggle={() => { pinChat(c.id); notify('info', pinnedIds.includes(c.id) ? 'Unpinned' : 'Pinned'); }}
                onDragStart={() => setDraggedChat(c.id)}
                onDragEnd={() => { setDraggedChat(null); setDragOver(null); }}
              />
            ))}
          </div>
        </div>
      </div>

      <div className="p-3 border-t border-[var(--border)] space-y-0.5 shrink-0">
        <SidebarNavItem
          collapsed={sidebarCollapsed}
          icon={<Menu className="w-4 h-4" />}
          label="Models"
          onClick={() => openDialog({ share: true })}
        />
        <SidebarNavItem
          collapsed={sidebarCollapsed}
          icon={<Settings className="w-4 h-4" />}
          label="Settings"
          onClick={() => openDialog({ settings: true })}
        />
        <SidebarNavItem
          collapsed={sidebarCollapsed}
          icon={<User className="w-4 h-4" />}
          label="Profile"
          onClick={() => openDialog({ profile: true })}
        />
        <SidebarNavItem
          collapsed={sidebarCollapsed}
          icon={sidebarCollapsed ? <PanelLeftOpen className="w-4 h-4" /> : <PanelLeftClose className="w-4 h-4" />}
          label={sidebarCollapsed ? 'Expand' : 'Collapse'}
          onClick={() => dispatch({ type: 'TOGGLE_SIDEBAR' })}
        />
        {!sidebarCollapsed && (
          <div className="mt-2 pt-2 border-t border-[var(--border)] flex items-center gap-2.5 px-2 py-1.5 rounded-lg hover:bg-white/5 cursor-pointer" onClick={() => openDialog({ profile: true })}>
            <div className="w-8 h-8 rounded-full bg-gradient-to-tr from-primary to-accent flex items-center justify-center text-white text-xs font-medium shadow-sm">
              {profile.avatar ? <img src={profile.avatar} className="w-full h-full rounded-full object-cover" alt="" /> : getInitials(profile.name)}
            </div>
            <div className="min-w-0 flex-1">
              <div className="text-sm font-medium text-[var(--foreground)] truncate">{profile.name}</div>
              <div className="text-[11px] text-gray-500 truncate">{profile.email}</div>
            </div>
          </div>
        )}
      </div>

      <SearchDialog open={dialog.search} onClose={closeDialogs} />
      <FolderCreateDialog open={dialog.folderCreate} onClose={closeDialogs} onCreate={addFolder} />
      {folderMenu && (
        <FolderMenuDialog
          folder={folders.find(f => f.id === folderMenu)!}
          onClose={() => setFolderMenu(null)}
          onRename={(name) => onRenameFolder(folderMenu, name)}
          onDelete={() => onDeleteFolder(folderMenu)}
        />
      )}
      {chatMenu && (() => {
        const chat = conversations.find(c => c.id === chatMenu.id);
        if (!chat) return null;
        return (
          <ChatContextMenu
            x={chatMenu.x}
            y={chatMenu.y}
            chat={chat}
            pinned={pinnedIds.includes(chat.id)}
            folders={folders}
            onClose={() => setChatMenu(null)}
            onPin={() => pinChat(chat.id)}
            onDelete={() => { dispatch({ type: 'OPEN_DIALOG', payload: { confirmDelete: chat.id } }); }}
            onMoveToFolder={(fid) => onChatDropToFolder(chat.id, fid)}
            onOpen={() => { selectChat(chat.id); setChatMenu(null); }}
            onRename={(title) => dispatch({ type: 'UPDATE_CONVERSATION', payload: { id: chat.id, updates: { title } } })}
          />
        );
      })()}
      <ConfirmDialog
        open={!!dialog.confirmDelete}
        title="Delete chat?"
        message="This conversation and its messages will be permanently deleted."
        confirmText="Delete"
        danger
        onCancel={closeDialogs}
        onConfirm={() => {
          if (dialog.confirmDelete) { deleteChat(dialog.confirmDelete); notify('success', 'Chat deleted'); }
          closeDialogs();
        }}
      />
    </>
  );

  return (
    <>
      <aside
        className={clsx(
          'hidden md:flex flex-shrink-0 flex-col bg-[var(--background)] border-r border-[var(--border)] h-screen text-gray-300 shrink-0 transition-[width] duration-200',
          sidebarCollapsed ? 'w-16' : 'w-64'
        )}
      >
        {sidebarContent}
      </aside>

      {sidebarMobileOpen && (
        <div className="md:hidden fixed inset-0 z-40">
          <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" onClick={() => dispatch({ type: 'TOGGLE_MOBILE_SIDEBAR', payload: false })} />
          <aside className="absolute left-0 top-0 h-full w-72 max-w-[85vw] flex flex-col bg-[var(--background)] border-r border-[var(--border)] text-gray-300 shadow-2xl animate-in slide-in-from-left duration-300">
            {sidebarContent}
          </aside>
        </div>
      )}
    </>
  );
}

function SectionTitle({ icon, label }: { icon: React.ReactNode; label: string }) {
  return (
    <div className="px-3 text-xs font-medium text-gray-500 mb-1.5 uppercase tracking-wider flex items-center gap-1.5">
      {icon}
      {label}
    </div>
  );
}

function SidebarNavItem({ collapsed, icon, label, onClick, active }: { collapsed: boolean; icon: React.ReactNode; label: string; onClick: () => void; active?: boolean }) {
  return (
    <button
      onClick={onClick}
      className={clsx(
        'w-full flex items-center gap-2 py-2 text-sm rounded-lg transition-colors truncate',
        collapsed ? 'justify-center px-0 w-10 h-10 mx-auto' : 'px-3',
        active ? 'bg-white/10 text-white font-medium' : 'text-gray-400 hover:text-white hover:bg-white/5'
      )}
      title={collapsed ? label : undefined}
    >
      {icon}
      {!collapsed && <span className="truncate">{label}</span>}
    </button>
  );
}

function FolderNavItem({ folder, collapsed, onToggle, onMenu }: { folder: FolderType; collapsed: boolean; onToggle: () => void; onMenu: () => void }) {
  return (
    <div className="flex items-center group">
      <button
        onClick={onToggle}
        className={clsx(
          'flex items-center gap-2 py-2 text-sm rounded-lg transition-colors truncate flex-1 min-w-0',
          collapsed ? 'justify-center w-10 h-10 mx-auto' : 'px-3',
          'text-gray-400 hover:text-[var(--foreground)] hover:bg-white/5'
        )}
        title={collapsed ? folder.name : undefined}
      >
        <span className="w-4 h-4 flex items-center justify-center flex-shrink-0" style={{ color: folder.color }}>
          {folder.expanded ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
        </span>
        <span className="w-4 h-4 flex items-center justify-center flex-shrink-0" style={{ color: folder.color }}>
          <Folder className="w-3.5 h-3.5" />
        </span>
        {!collapsed && <span className="truncate">{folder.name}</span>}
      </button>
      {!collapsed && (
        <button
          onClick={(e) => { e.stopPropagation(); onMenu(); }}
          className="p-1.5 rounded text-gray-500 hover:text-[var(--foreground)] hover:bg-white/10 opacity-0 group-hover:opacity-100 transition-opacity"
          aria-label="Folder options"
        >
          <MoreHorizontal className="w-3.5 h-3.5" />
        </button>
      )}
    </div>
  );
}

function ChatNavItem({
  conversation, active, onClick, query, pinned, onMenu, onPinToggle, onDragStart, onDragEnd, small
}: {
  conversation: Conversation; active?: boolean; onClick: () => void; query?: string; pinned?: boolean;
  onMenu: (x: number, y: number) => void; onPinToggle: () => void;
  onDragStart: () => void; onDragEnd: () => void; small?: boolean;
}) {
  return (
    <div
      draggable
      onDragStart={(e) => { e.dataTransfer.effectAllowed = 'move'; onDragStart(); }}
      onDragEnd={onDragEnd}
      className="group relative"
    >
      <button
        onClick={onClick}
        className={clsx(
          'w-full text-left rounded-lg transition-all flex items-center gap-2',
          small ? 'px-2.5 py-1.5 text-xs' : 'px-3 py-2 text-sm',
          active ? 'bg-white/10 text-[var(--foreground)] font-medium' : 'text-gray-400 hover:text-[var(--foreground)] hover:bg-white/5',
        )}
      >
        <span className="w-3.5 h-3.5 flex-shrink-0 text-gray-500">
          {pinned ? <Pin className="w-3.5 h-3.5 text-primary fill-primary/20" /> : <Clock className="w-3.5 h-3.5" />}
        </span>
        <span className="truncate flex-1 min-w-0">
          {renderHighlightedText(conversation.title, query || '')}
        </span>
        <span className="text-[10px] text-gray-500 flex-shrink-0 ml-1 whitespace-nowrap">
          {formatTimestamp(conversation.updatedAt)}
        </span>
      </button>
      <div className="absolute right-1 top-1/2 -translate-y-1/2 opacity-0 group-hover:opacity-100 transition-opacity flex items-center gap-0.5">
        <button
          onClick={(e) => { e.stopPropagation(); onPinToggle(); }}
          className="p-1 rounded hover:bg-white/10 text-gray-500 hover:text-[var(--foreground)]"
          title={pinned ? 'Unpin' : 'Pin'}
          aria-label="Toggle pin"
        >
          <Pin className={clsx('w-3 h-3', pinned && 'fill-primary text-primary')} />
        </button>
        <button
          onClick={(e) => {
            e.stopPropagation();
            const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
            onMenu(rect.right + 4, rect.bottom - 4);
          }}
          className="p-1 rounded hover:bg-white/10 text-gray-500 hover:text-[var(--foreground)]"
          title="More"
          aria-label="Chat options"
        >
          <MoreHorizontal className="w-3 h-3" />
        </button>
      </div>
    </div>
  );
}

function renderHighlightedText(text: string, query: string) {
  const segments = query ? highlightText(text, query) : [{ text, highlight: false }];
  return (
    <>
      {segments.map((segment, index) => (
        <span
          key={`${segment.text}-${index}`}
          className={segment.highlight ? 'text-primary font-semibold' : undefined}
        >
          {segment.text}
        </span>
      ))}
    </>
  );
}

function SearchDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { conversations, pinnedIds, selectChat } = useApp();
  const [q, setQ] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  const results = useMemo(() => {
    if (!q.trim()) return [];
    const query = q.toLowerCase();
    return conversations
      .filter(c =>
        c.title.toLowerCase().includes(query) ||
        c.messages.some(m => m.content.toLowerCase().includes(query))
      )
      .slice(0, 20);
  }, [conversations, q]);

  return (
    <Modal open={open} onClose={onClose} size="lg" hideClose title="Search Chats">
      <div className="p-2">
        <div className="relative px-2">
          <Search className="absolute left-5 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
          <input
            ref={inputRef}
            value={q}
            onChange={e => setQ(e.target.value)}
            placeholder="Search titles, messages, tags..."
            autoFocus
            className="w-full pl-10 pr-16 py-3 bg-[var(--surface)] border border-[var(--border)] rounded-xl text-[var(--foreground)] outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary/50"
          />
        </div>
        <div className="mt-3 max-h-[60vh] overflow-y-auto scrollbar-hide px-2 space-y-0.5">
          {!q.trim() && (
            <div className="p-8 text-center text-sm text-gray-500">
              <Search className="w-8 h-8 mx-auto mb-2 opacity-50" />
              <p>Type to search across all conversations</p>
            </div>
          )}
          {q.trim() && results.length === 0 && (
            <div className="p-8 text-center text-sm text-gray-500">
              <Filter className="w-8 h-8 mx-auto mb-2 opacity-50" />
              <p>No results for "{q}"</p>
            </div>
          )}
          {results.map(c => {
            const matchMsg = c.messages.find(m => m.content.toLowerCase().includes(q.toLowerCase()));
            return (
              <button
                key={c.id}
                onClick={() => { selectChat(c.id); onClose(); }}
                className="w-full text-left p-3 rounded-xl hover:bg-white/5 border border-transparent hover:border-[var(--border)] transition-all flex gap-3"
              >
                <div className={clsx('w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0', pinnedIds.includes(c.id) ? 'bg-primary/20 text-primary' : 'bg-white/5 text-gray-400')}>
                  {pinnedIds.includes(c.id) ? <Pin className="w-4 h-4" /> : <MessageSquarePlus className="w-4 h-4" />}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="font-medium text-sm text-[var(--foreground)] truncate">{renderHighlightedText(c.title, q)}</div>
                  {matchMsg && (
                    <div className="text-xs text-gray-500 mt-0.5 truncate">{renderHighlightedText(truncate(matchMsg.content, 120), q)}</div>
                  )}
                  <div className="text-[10px] text-gray-600 mt-1">{formatTimestamp(c.updatedAt)} · {c.messages.length} messages</div>
                </div>
              </button>
            );
          })}
        </div>
      </div>
    </Modal>
  );
}

function FolderCreateDialog({ open, onClose, onCreate }: { open: boolean; onClose: () => void; onCreate: (name: string) => void }) {
  const [name, setName] = useState('');
  useEffect(() => { if (open) setName(''); }, [open]);
  return (
    <Modal open={open} onClose={onClose} size="sm" title="Create Folder">
      <div className="p-6 space-y-4">
        <div>
          <label className="block text-xs font-medium text-gray-400 mb-1.5">Folder Name</label>
          <input
            autoFocus
            value={name}
            onChange={e => setName(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && name.trim() && onCreate(name.trim())}
            placeholder="Work, Projects, Ideas..."
            className="w-full px-3 py-2.5 bg-[var(--surface)] border border-[var(--border)] rounded-xl text-[var(--foreground)] outline-none focus:ring-2 focus:ring-primary/30"
          />
        </div>
        <div className="flex gap-2 justify-end">
          <button onClick={onClose} className="px-4 py-2 rounded-lg text-sm text-gray-300 hover:bg-white/5 border border-white/10 transition-colors">Cancel</button>
          <button
            onClick={() => name.trim() && onCreate(name.trim())}
            disabled={!name.trim()}
            className="px-4 py-2 rounded-lg text-sm bg-primary text-white hover:bg-primary/90 transition-colors disabled:opacity-50"
          >Create</button>
        </div>
      </div>
    </Modal>
  );
}

function FolderMenuDialog({ folder, onClose, onRename, onDelete }: { folder: FolderType; onClose: () => void; onRename: (name: string) => void; onDelete: () => void }) {
  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState(folder.name);
  return (
    <Modal open onClose={onClose} size="sm" title={`Folder: ${folder.name}`}>
      <div className="p-6 space-y-3">
        {renaming ? (
          <>
            <input
              autoFocus
              value={name}
              onChange={e => setName(e.target.value)}
              className="w-full px-3 py-2.5 bg-[var(--surface)] border border-[var(--border)] rounded-xl text-[var(--foreground)] outline-none focus:ring-2 focus:ring-primary/30"
            />
            <div className="flex gap-2 justify-end">
              <button onClick={() => setRenaming(false)} className="px-3 py-2 text-sm rounded-lg hover:bg-white/5 text-gray-300">Cancel</button>
              <button onClick={() => name.trim() && onRename(name.trim())} disabled={!name.trim()} className="px-3 py-2 text-sm rounded-lg bg-primary text-white hover:bg-primary/90 disabled:opacity-50">Save</button>
            </div>
          </>
        ) : (
          <>
            <div className="flex items-center gap-3 p-3 rounded-xl bg-white/5">
              <div className="w-10 h-10 rounded-lg flex items-center justify-center" style={{ color: folder.color, background: `${folder.color}15` }}>
                <Folder className="w-5 h-5" />
              </div>
              <div>
                <div className="font-medium text-[var(--foreground)]">{folder.name}</div>
                <div className="text-xs text-gray-500">Created {formatTimestamp(folder.createdAt)}</div>
              </div>
            </div>
            <button onClick={() => setRenaming(true)} className="w-full flex items-center gap-2.5 px-3 py-2.5 text-sm rounded-lg hover:bg-white/5 text-gray-200">
              <Edit2 className="w-4 h-4" /> Rename
            </button>
            <button onClick={onDelete} className="w-full flex items-center gap-2.5 px-3 py-2.5 text-sm rounded-lg hover:bg-danger/10 text-danger">
              <Trash2 className="w-4 h-4" /> Delete Folder
            </button>
          </>
        )}
      </div>
    </Modal>
  );
}

function ChatContextMenu({ x, y, chat, pinned, folders, onClose, onPin, onDelete, onMoveToFolder, onOpen, onRename }: {
  x: number; y: number; chat: Conversation; pinned: boolean; folders: FolderType[]; onClose: () => void;
  onPin: () => void; onDelete: () => void; onMoveToFolder: (folderId: string | null) => void; onOpen: () => void;
  onRename: (title: string) => void;
}) {
  const [renameOpen, setRenameOpen] = useState(false);
  const [newTitle, setNewTitle] = useState(chat.title);
  if (renameOpen) {
    return (
      <Modal open onClose={() => { setRenameOpen(false); onClose(); }} size="sm" title="Rename Chat">
        <div className="p-6 space-y-3">
          <input
            autoFocus value={newTitle}
            onChange={e => setNewTitle(e.target.value)}
            className="w-full px-3 py-2.5 bg-[var(--surface)] border border-[var(--border)] rounded-xl text-[var(--foreground)] outline-none focus:ring-2 focus:ring-primary/30"
          />
          <div className="flex gap-2 justify-end">
            <button onClick={() => { setRenameOpen(false); onClose(); }} className="px-3 py-2 rounded-lg text-sm hover:bg-white/5 text-gray-300">Cancel</button>
            <button onClick={() => { onRename(newTitle.trim() || chat.title); setRenameOpen(false); onClose(); }} className="px-3 py-2 rounded-lg text-sm bg-primary text-white hover:bg-primary/90">Save</button>
          </div>
        </div>
      </Modal>
    );
  }
  return (
    <>
      <div className="fixed inset-0 z-40" onClick={onClose} />
      <div
        style={{ top: Math.min(y, window.innerHeight - 400), left: Math.min(x, window.innerWidth - 260) }}
        className="fixed z-50 min-w-[240px] bg-[var(--card)] border border-[var(--border)] rounded-xl shadow-2xl py-1.5 animate-in fade-in zoom-in-95 duration-100"
      >
        <MenuItem icon={<Star className="w-4 h-4" />} label="Open chat" onClick={onOpen} />
        <MenuItem icon={pinned ? <Pin className="w-4 h-4 fill-primary text-primary" /> : <Pin className="w-4 h-4" />} label={pinned ? 'Unpin' : 'Pin'} onClick={onPin} />
        <MenuItem icon={<Edit2 className="w-4 h-4" />} label="Rename" onClick={() => setRenameOpen(true)} />
        <div className="my-1 border-t border-[var(--border)]" />
        <div className="px-3 py-1.5 text-[10px] font-semibold uppercase tracking-wider text-gray-500">Move to folder</div>
        <MenuItem icon={<Folder className="w-4 h-4" />} label="No folder" onClick={() => onMoveToFolder(null)} />
        {folders.map(f => (
          <MenuItem
            key={f.id}
            icon={<Folder className="w-4 h-4" />}
            label={f.name}
            color={f.color}
            selected={chat.folderId === f.id}
            onClick={() => onMoveToFolder(f.id)}
          />
        ))}
        <div className="my-1 border-t border-[var(--border)]" />
        <MenuItem icon={<Trash2 className="w-4 h-4" />} label="Delete" danger onClick={onDelete} />
      </div>
    </>
  );
}

function MenuItem({ icon, label, onClick, danger, color, selected }: { icon?: React.ReactNode; label: string; onClick: () => void; danger?: boolean; color?: string; selected?: boolean }) {
  return (
    <button
      onClick={onClick}
      className={clsx(
        'w-full flex items-center gap-2.5 px-3 py-2 text-sm text-left transition-colors',
        danger ? 'text-danger hover:bg-danger/10' : 'text-gray-200 hover:bg-white/10',
        selected && 'bg-white/5'
      )}
    >
      <span className="w-4 h-4 flex items-center justify-center" style={color ? { color } : undefined}>{icon}</span>
      <span className="flex-1">{label}</span>
      {selected && <div className="w-1.5 h-1.5 rounded-full bg-primary" />}
    </button>
  );
}
