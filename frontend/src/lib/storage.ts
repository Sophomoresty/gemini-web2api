import type { Conversation, Folder, Profile, Settings, NotificationItem } from '../types';

const PREFIX = 'omniai:';

export const storage = {
  get<T>(key: string, defaultValue: T): T {
    try {
      const raw = localStorage.getItem(PREFIX + key);
      if (raw === null) return defaultValue;
      return JSON.parse(raw) as T;
    } catch {
      return defaultValue;
    }
  },

  set<T>(key: string, value: T): void {
    try {
      localStorage.setItem(PREFIX + key, JSON.stringify(value));
    } catch {
      console.warn('Failed to write to localStorage');
    }
  },

  remove(key: string): void {
    localStorage.removeItem(PREFIX + key);
  },
};

export const KEYS = {
  CONVERSATIONS: 'conversations',
  FOLDERS: 'folders',
  CURRENT_CHAT_ID: 'currentChatId',
  PINNED_IDS: 'pinnedIds',
  PROFILE: 'profile',
  SETTINGS: 'settings',
  NOTIFICATIONS: 'notifications',
  MODEL: 'selectedModel',
  SIDEBAR_COLLAPSED: 'sidebarCollapsed',
};

export const DEFAULT_SETTINGS: Settings = {
  theme: 'dark',
  language: 'en',
  timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
  streaming: true,
  animations: true,
  sidebarCollapsed: false,
  notifications: {
    enabled: true,
    sound: false,
    desktop: false,
  },
  modelDefaults: {
    model: 'gemini-3.5-flash-thinking',
    temperature: 0.7,
    maxTokens: 4096,
    topP: 0.9,
  },
  shortcuts: {
    search: 'Ctrl+K',
    newChat: 'Ctrl+N',
    toggleSidebar: 'Ctrl+Shift+L',
    closeDialog: 'Esc',
  },
};

export const DEFAULT_PROFILE: Profile = {
  name: 'User',
  email: 'user@example.com',
};

export const DEFAULT_FOLDERS: Folder[] = [
  { id: 'folder-work', name: 'Work', color: '#2563EB', expanded: true, createdAt: Date.now() },
  { id: 'folder-personal', name: 'Personal', color: '#22C55E', expanded: true, createdAt: Date.now() + 1 },
];

export function loadConversations(): Conversation[] {
  return storage.get<Conversation[]>(KEYS.CONVERSATIONS, []);
}

export function saveConversations(conversations: Conversation[]): void {
  storage.set(KEYS.CONVERSATIONS, conversations);
}

export function loadFolders(): Folder[] {
  return storage.get<Folder[]>(KEYS.FOLDERS, DEFAULT_FOLDERS);
}

export function saveFolders(folders: Folder[]): void {
  storage.set(KEYS.FOLDERS, folders);
}

export function loadPinnedIds(): string[] {
  return storage.get<string[]>(KEYS.PINNED_IDS, []);
}

export function savePinnedIds(ids: string[]): void {
  storage.set(KEYS.PINNED_IDS, ids);
}

export function loadProfile(): Profile {
  return storage.get<Profile>(KEYS.PROFILE, DEFAULT_PROFILE);
}

export function saveProfile(profile: Profile): void {
  storage.set(KEYS.PROFILE, profile);
}

export function loadSettings(): Settings {
  const saved = storage.get<Partial<Settings>>(KEYS.SETTINGS, {});
  return { ...DEFAULT_SETTINGS, ...saved, notifications: { ...DEFAULT_SETTINGS.notifications, ...saved.notifications }, modelDefaults: { ...DEFAULT_SETTINGS.modelDefaults, ...saved.modelDefaults } };
}

export function saveSettings(settings: Settings): void {
  storage.set(KEYS.SETTINGS, settings);
}

export function loadNotifications(): NotificationItem[] {
  return storage.get<NotificationItem[]>(KEYS.NOTIFICATIONS, []);
}

export function saveNotifications(notifications: NotificationItem[]): void {
  storage.set(KEYS.NOTIFICATIONS, notifications.slice(0, 50));
}
