export interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: number;
  thinking?: boolean;
  error?: boolean;
  tokenUsage?: TokenUsage;
  edited?: boolean;
  renderMode?: 'markdown' | 'json';
}

export interface TokenUsage {
  promptTokens: number;
  completionTokens: number;
  totalTokens: number;
}

export interface Conversation {
  id: string;
  title: string;
  messages: Message[];
  model: string;
  createdAt: number;
  updatedAt: number;
  pinned?: boolean;
  folderId?: string | null;
  tags?: string[];
  scrollPosition?: number;
}

export interface Folder {
  id: string;
  name: string;
  color?: string;
  expanded?: boolean;
  createdAt: number;
}

export interface Profile {
  name: string;
  email: string;
  avatar?: string;
}

export interface Settings {
  theme: 'dark' | 'light' | 'system';
  language: string;
  timezone: string;
  streaming: boolean;
  animations: boolean;
  sidebarCollapsed: boolean;
  notifications: {
    enabled: boolean;
    sound: boolean;
    desktop: boolean;
  };
  modelDefaults: {
    model: string;
    temperature: number;
    maxTokens: number;
    topP: number;
  };
  shortcuts: Record<string, string>;
}

export interface NotificationItem {
  id: string;
  type: 'success' | 'error' | 'info' | 'warning';
  title: string;
  message?: string;
  timestamp: number;
  read?: boolean;
}

export interface UploadedFile {
  id: string;
  name: string;
  size: number;
  type: string;
  progress: number;
  data?: string;
  error?: string;
}

export type View = 'chat' | 'settings' | 'profile' | 'models' | 'search';
