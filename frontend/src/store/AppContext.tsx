import React, { createContext, useContext, useReducer, useEffect, useCallback, useMemo, useRef } from 'react';
import type { Conversation, Folder, Profile, Settings, NotificationItem, Message, View } from '../types';
import { generateId } from '../lib/utils';
import {
  loadConversations, saveConversations,
  loadFolders, saveFolders,
  loadPinnedIds, savePinnedIds,
  loadProfile, saveProfile,
  loadSettings, saveSettings,
  loadNotifications, saveNotifications,
  storage, KEYS,
} from '../lib/storage';

interface AppState {
  view: View;
  conversations: Conversation[];
  folders: Folder[];
  pinnedIds: string[];
  profile: Profile;
  settings: Settings;
  notifications: NotificationItem[];
  currentChatId: string | null;
  selectedModel: string;
  sidebarCollapsed: boolean;
  sidebarMobileOpen: boolean;
  backendAvailable: boolean;
  dialog: DialogState;
}

interface DialogState {
  settings: boolean;
  profile: boolean;
  search: boolean;
  share: boolean;
  folderCreate: boolean;
  folderEdit: string | null;
  notifications: boolean;
  confirmDelete: string | null;
}

type Action =
  | { type: 'SET_VIEW'; payload: View }
  | { type: 'HYDRATE'; payload: Partial<AppState> }
  | { type: 'ADD_CONVERSATION'; payload: Conversation }
  | { type: 'UPDATE_CONVERSATION'; payload: { id: string; updates: Partial<Conversation> } }
  | { type: 'DELETE_CONVERSATION'; payload: string }
  | { type: 'SET_CURRENT_CHAT'; payload: string | null }
  | { type: 'ADD_MESSAGE'; payload: { chatId: string; message: Message } }
  | { type: 'UPDATE_MESSAGE'; payload: { chatId: string; messageId: string; updates: Partial<Message> } }
  | { type: 'DELETE_MESSAGE'; payload: { chatId: string; messageId: string } }
  | { type: 'SET_MESSAGES'; payload: { chatId: string; messages: Message[] } }
  | { type: 'TOGGLE_PIN'; payload: string }
  | { type: 'SET_PINNED_ORDER'; payload: string[] }
  | { type: 'ADD_FOLDER'; payload: Folder }
  | { type: 'UPDATE_FOLDER'; payload: { id: string; updates: Partial<Folder> } }
  | { type: 'DELETE_FOLDER'; payload: string }
  | { type: 'MOVE_TO_FOLDER'; payload: { chatId: string; folderId: string | null } }
  | { type: 'UPDATE_PROFILE'; payload: Partial<Profile> }
  | { type: 'UPDATE_SETTINGS'; payload: Partial<Settings> }
  | { type: 'SELECT_MODEL'; payload: string }
  | { type: 'TOGGLE_SIDEBAR'; payload?: boolean }
  | { type: 'TOGGLE_MOBILE_SIDEBAR'; payload?: boolean }
  | { type: 'ADD_NOTIFICATION'; payload: NotificationItem }
  | { type: 'MARK_NOTIFICATION_READ'; payload: string }
  | { type: 'CLEAR_NOTIFICATIONS' }
  | { type: 'OPEN_DIALOG'; payload: Partial<DialogState> }
  | { type: 'CLOSE_ALL_DIALOGS' }
  | { type: 'SET_BACKEND_AVAILABLE'; payload: boolean };

const initialDialog: DialogState = {
  settings: false,
  profile: false,
  search: false,
  share: false,
  folderCreate: false,
  folderEdit: null,
  notifications: false,
  confirmDelete: null,
};

const initialState: AppState = {
  view: 'chat',
  conversations: [],
  folders: [],
  pinnedIds: [],
  profile: { name: 'User', email: 'user@example.com' },
  settings: {
    theme: 'dark',
    language: 'en',
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    streaming: true,
    animations: true,
    sidebarCollapsed: false,
    notifications: { enabled: true, sound: false, desktop: false },
    modelDefaults: { model: 'gemini-3.5-flash-thinking', temperature: 0.7, maxTokens: 4096, topP: 0.9 },
    shortcuts: { search: 'Ctrl+K', newChat: 'Ctrl+N', toggleSidebar: 'Ctrl+Shift+L', closeDialog: 'Esc' },
  },
  notifications: [],
  currentChatId: null,
  selectedModel: 'gemini-3.5-flash-thinking',
  sidebarCollapsed: false,
  sidebarMobileOpen: false,
  backendAvailable: true,
  dialog: initialDialog,
};

function reducer(state: AppState, action: Action): AppState {
  switch (action.type) {
    case 'SET_VIEW':
      return { ...state, view: action.payload };

    case 'HYDRATE':
      return { ...state, ...action.payload };

    case 'ADD_CONVERSATION': {
      const newConvs = [action.payload, ...state.conversations];
      saveConversations(newConvs);
      return { ...state, conversations: newConvs, currentChatId: action.payload.id, view: 'chat' };
    }

    case 'UPDATE_CONVERSATION': {
      const updated = state.conversations.map(c =>
        c.id === action.payload.id ? { ...c, ...action.payload.updates, updatedAt: Date.now() } : c
      );
      saveConversations(updated);
      return { ...state, conversations: updated };
    }

    case 'DELETE_CONVERSATION': {
      const filtered = state.conversations.filter(c => c.id !== action.payload);
      saveConversations(filtered);
      const newPinned = state.pinnedIds.filter(id => id !== action.payload);
      savePinnedIds(newPinned);
      const newCurrent = state.currentChatId === action.payload ? null : state.currentChatId;
      return {
        ...state,
        conversations: filtered,
        pinnedIds: newPinned,
        currentChatId: newCurrent,
      };
    }

    case 'SET_CURRENT_CHAT':
      return { ...state, currentChatId: action.payload, view: 'chat' };

    case 'ADD_MESSAGE': {
      const updated = state.conversations.map(c => {
        if (c.id !== action.payload.chatId) return c;
        return { ...c, messages: [...c.messages, action.payload.message], updatedAt: Date.now() };
      });
      saveConversations(updated);
      return { ...state, conversations: updated };
    }

    case 'UPDATE_MESSAGE': {
      const updated = state.conversations.map(c => {
        if (c.id !== action.payload.chatId) return c;
        return {
          ...c,
          messages: c.messages.map(m =>
            m.id === action.payload.messageId ? { ...m, ...action.payload.updates } : m
          ),
          updatedAt: Date.now(),
        };
      });
      saveConversations(updated);
      return { ...state, conversations: updated };
    }

    case 'DELETE_MESSAGE': {
      const updated = state.conversations.map(c => {
        if (c.id !== action.payload.chatId) return c;
        return {
          ...c,
          messages: c.messages.filter(m => m.id !== action.payload.messageId),
          updatedAt: Date.now(),
        };
      });
      saveConversations(updated);
      return { ...state, conversations: updated };
    }

    case 'SET_MESSAGES': {
      const updated = state.conversations.map(c => {
        if (c.id !== action.payload.chatId) return c;
        return { ...c, messages: action.payload.messages, updatedAt: Date.now() };
      });
      saveConversations(updated);
      return { ...state, conversations: updated };
    }

    case 'TOGGLE_PIN': {
      const isPinned = state.pinnedIds.includes(action.payload);
      const newPinned = isPinned
        ? state.pinnedIds.filter(id => id !== action.payload)
        : [...state.pinnedIds, action.payload];
      savePinnedIds(newPinned);
      return { ...state, pinnedIds: newPinned };
    }

    case 'SET_PINNED_ORDER':
      savePinnedIds(action.payload);
      return { ...state, pinnedIds: action.payload };

    case 'ADD_FOLDER': {
      const folders = [...state.folders, action.payload];
      saveFolders(folders);
      return { ...state, folders };
    }

    case 'UPDATE_FOLDER': {
      const folders = state.folders.map(f =>
        f.id === action.payload.id ? { ...f, ...action.payload.updates } : f
      );
      saveFolders(folders);
      return { ...state, folders };
    }

    case 'DELETE_FOLDER': {
      const folders = state.folders.filter(f => f.id !== action.payload);
      saveFolders(folders);
      const convs = state.conversations.map(c =>
        c.folderId === action.payload ? { ...c, folderId: null } : c
      );
      saveConversations(convs);
      return { ...state, folders, conversations: convs };
    }

    case 'MOVE_TO_FOLDER': {
      const convs = state.conversations.map(c =>
        c.id === action.payload.chatId ? { ...c, folderId: action.payload.folderId } : c
      );
      saveConversations(convs);
      return { ...state, conversations: convs };
    }

    case 'UPDATE_PROFILE': {
      const profile = { ...state.profile, ...action.payload };
      saveProfile(profile);
      return { ...state, profile };
    }

    case 'UPDATE_SETTINGS': {
      const settings = { ...state.settings, ...action.payload };
      saveSettings(settings);
      return { ...state, settings };
    }

    case 'SELECT_MODEL':
      storage.set(KEYS.MODEL, action.payload);
      return { ...state, selectedModel: action.payload };

    case 'TOGGLE_SIDEBAR': {
      const collapsed = action.payload ?? !state.sidebarCollapsed;
      storage.set(KEYS.SIDEBAR_COLLAPSED, collapsed);
      return { ...state, sidebarCollapsed: collapsed };
    }

    case 'TOGGLE_MOBILE_SIDEBAR':
      return { ...state, sidebarMobileOpen: action.payload ?? !state.sidebarMobileOpen };

    case 'ADD_NOTIFICATION': {
      const notifications = [action.payload, ...state.notifications].slice(0, 50);
      saveNotifications(notifications);
      return { ...state, notifications };
    }

    case 'MARK_NOTIFICATION_READ': {
      const notifications = state.notifications.map(n =>
        n.id === action.payload ? { ...n, read: true } : n
      );
      saveNotifications(notifications);
      return { ...state, notifications };
    }

    case 'CLEAR_NOTIFICATIONS':
      saveNotifications([]);
      return { ...state, notifications: [] };

    case 'OPEN_DIALOG':
      return { ...state, dialog: { ...state.dialog, ...action.payload } };

    case 'CLOSE_ALL_DIALOGS':
      return { ...state, dialog: initialDialog };

    case 'SET_BACKEND_AVAILABLE':
      return { ...state, backendAvailable: action.payload };

    default:
      return state;
  }
}

interface AppContextValue extends AppState {
  dispatch: React.Dispatch<Action>;
  createNewChat: (modelOverride?: string) => string;
  selectChat: (id: string) => void;
  deleteChat: (id: string) => void;
  pinChat: (id: string) => void;
  notify: (type: NotificationItem['type'], title: string, message?: string) => void;
  openDialog: (dialog: Partial<DialogState>) => void;
  closeDialogs: () => void;
  getCurrentConversation: () => Conversation | null;
}

const AppContext = createContext<AppContextValue | null>(null);

export function AppProvider({ children }: { children: React.ReactNode }) {
  const [state, dispatch] = useReducer(reducer, initialState);
  const initialized = useRef(false);

  useEffect(() => {
    if (initialized.current) return;
    initialized.current = true;
    const conversations = loadConversations();
    const folders = loadFolders();
    const pinnedIds = loadPinnedIds();
    const profile = loadProfile();
    const settings = loadSettings();
    const notifications = loadNotifications();
    const selectedModel = storage.get<string>(KEYS.MODEL, settings.modelDefaults.model);
    const sidebarCollapsed = storage.get<boolean>(KEYS.SIDEBAR_COLLAPSED, false);
    const currentChatId = storage.get<string | null>(KEYS.CURRENT_CHAT_ID, null);
    dispatch({ type: 'HYDRATE', payload: { conversations, folders, pinnedIds, profile, settings, notifications, selectedModel, sidebarCollapsed, currentChatId: conversations.length && conversations.some(c => c.id === currentChatId) ? currentChatId : null } });
    const applyTheme = (theme: Settings['theme']) => {
      const root = document.documentElement;
      const effective = theme === 'system' ? (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light') : theme;
      root.classList.toggle('dark', effective === 'dark');
      root.classList.toggle('light', effective === 'light');
    };
    applyTheme(settings.theme);
  }, []);

  useEffect(() => {
    if (state.currentChatId) storage.set(KEYS.CURRENT_CHAT_ID, state.currentChatId);
  }, [state.currentChatId]);

  useEffect(() => {
    const root = document.documentElement;
    const effective = state.settings.theme === 'system'
      ? (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
      : state.settings.theme;
    root.classList.toggle('dark', effective === 'dark');
    root.classList.toggle('light', effective === 'light');
  }, [state.settings.theme]);

  const createNewChat = useCallback((modelOverride?: string): string => {
    const id = generateId();
    const model = modelOverride || state.selectedModel || state.settings.modelDefaults.model;
    const conv: Conversation = {
      id,
      title: 'New Chat',
      messages: [],
      model,
      createdAt: Date.now(),
      updatedAt: Date.now(),
      folderId: null,
      tags: [],
    };
    dispatch({ type: 'ADD_CONVERSATION', payload: conv });
    return id;
  }, [state.selectedModel, state.settings.modelDefaults.model]);

  const selectChat = useCallback((id: string) => {
    dispatch({ type: 'SET_CURRENT_CHAT', payload: id });
  }, []);

  const deleteChat = useCallback((id: string) => {
    dispatch({ type: 'DELETE_CONVERSATION', payload: id });
  }, []);

  const pinChat = useCallback((id: string) => {
    dispatch({ type: 'TOGGLE_PIN', payload: id });
  }, []);

  const notify = useCallback((type: NotificationItem['type'], title: string, message?: string) => {
    dispatch({
      type: 'ADD_NOTIFICATION',
      payload: { id: generateId(), type, title, message, timestamp: Date.now(), read: false },
    });
  }, []);

  const openDialog = useCallback((dialog: Partial<DialogState>) => {
    dispatch({ type: 'OPEN_DIALOG', payload: dialog });
  }, []);

  const closeDialogs = useCallback(() => {
    dispatch({ type: 'CLOSE_ALL_DIALOGS' });
  }, []);

  const getCurrentConversation = useCallback((): Conversation | null => {
    if (!state.currentChatId) return null;
    return state.conversations.find(c => c.id === state.currentChatId) || null;
  }, [state.currentChatId, state.conversations]);

  const value = useMemo<AppContextValue>(() => ({
    ...state,
    dispatch,
    createNewChat,
    selectChat,
    deleteChat,
    pinChat,
    notify,
    openDialog,
    closeDialogs,
    getCurrentConversation,
  }), [state, createNewChat, selectChat, deleteChat, pinChat, notify, openDialog, closeDialogs, getCurrentConversation]);

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

export function useApp() {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error('useApp must be used within AppProvider');
  return ctx;
}
