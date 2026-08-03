import type { Message, TokenUsage } from '../types';

const API_BASE = 'http://localhost:8081/v1';

export interface ChatCompletionChunk {
  choices: Array<{
    delta: { content?: string; reasoning_content?: string };
    index: number;
    finish_reason?: string | null;
  }>;
  model?: string;
  usage?: {
    prompt_tokens?: number;
    completion_tokens?: number;
    total_tokens?: number;
  };
}

export interface ChatCompletionResponse {
  choices: Array<{
    message: { role: string; content: string };
    finish_reason: string;
  }>;
  usage?: {
    prompt_tokens?: number;
    completion_tokens?: number;
    total_tokens?: number;
  };
}

export async function listModels(): Promise<string[]> {
  try {
    const res = await fetch(`${API_BASE}/models`, {
      headers: { 'Authorization': 'Bearer dummy' },
    });
    if (!res.ok) return [];
    const data = await res.json();
    return (data.data || []).map((m: any) => m.id);
  } catch {
    return [];
  }
}

export async function checkBackendHealth(): Promise<boolean> {
  try {
    const res = await fetch(`${API_BASE}/models`, {
      headers: { 'Authorization': 'Bearer dummy' },
      signal: AbortSignal.timeout(3000),
    });
    return res.ok;
  } catch {
    return false;
  }
}

export async function streamChatCompletion(
  messages: Message[],
  model: string,
  settings: { temperature: number; maxTokens: number; topP: number; stream: boolean },
  onChunk: (text: string, reasoningText: string) => void,
  signal: AbortSignal
): Promise<{ content: string; reasoning: string; usage: TokenUsage | null }> {
  let fullContent = '';
  let fullReasoning = '';
  let usage: TokenUsage | null = null;

  const response = await fetch(`${API_BASE}/chat/completions`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': 'Bearer dummy',
    },
    body: JSON.stringify({
      model,
      messages: messages.map(m => ({ role: m.role, content: m.content })),
      stream: settings.stream,
      temperature: settings.temperature,
      max_tokens: settings.maxTokens,
      top_p: settings.topP,
    }),
    signal,
  });

  if (!response.ok) {
    const errorText = await response.text().catch(() => 'Unknown error');
    throw new Error(`API Error (${response.status}): ${errorText || response.statusText}`);
  }

  if (!settings.stream) {
    const data: ChatCompletionResponse = await response.json();
    const content = data.choices[0]?.message?.content || '';
    fullContent = content;
    onChunk(content, '');
    if (data.usage) {
      usage = {
        promptTokens: data.usage.prompt_tokens || 0,
        completionTokens: data.usage.completion_tokens || 0,
        totalTokens: data.usage.total_tokens || 0,
      };
    }
    return { content: fullContent, reasoning: fullReasoning, usage };
  }

  if (!response.body) {
    throw new Error('No response body from server');
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() || '';

    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      if (trimmed === 'data: [DONE]') continue;
      if (!trimmed.startsWith('data:')) continue;

      try {
        const jsonStr = trimmed.slice(5).trim();
        const chunk: ChatCompletionChunk = JSON.parse(jsonStr);
        const delta = chunk.choices[0]?.delta;
        if (delta) {
          if (delta.content) {
            fullContent += delta.content;
            onChunk(delta.content, '');
          }
          if (delta.reasoning_content) {
            fullReasoning += delta.reasoning_content;
            onChunk('', delta.reasoning_content);
          }
        }
        if (chunk.usage) {
          usage = {
            promptTokens: chunk.usage.prompt_tokens || 0,
            completionTokens: chunk.usage.completion_tokens || 0,
            totalTokens: chunk.usage.total_tokens || 0,
          };
        }
      } catch (e) {
        console.warn('Failed to parse SSE chunk:', trimmed.slice(0, 100));
      }
    }
  }

  return { content: fullContent, reasoning: fullReasoning, usage };
}

export async function generateTitle(initialMessage: string, model: string): Promise<string> {
  try {
    const response = await fetch(`${API_BASE}/chat/completions`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer dummy',
      },
      body: JSON.stringify({
        model: model || 'gemini-flash-lite',
        messages: [
          {
            role: 'user',
            content: `Generate a concise title (max 8 words, no quotes) for a conversation starting with: "${initialMessage.slice(0, 500)}"`,
          },
        ],
        stream: false,
        max_tokens: 50,
        temperature: 0.5,
      }),
      signal: AbortSignal.timeout(10000),
    });

    if (!response.ok) throw new Error('Title generation failed');
    const data = await response.json();
    let title = data.choices?.[0]?.message?.content?.trim() || '';
    title = title.replace(/^["']|["']$/g, '').replace(/\.$/, '');
    return title.slice(0, 60) || 'New Chat';
  } catch {
    return initialMessage.slice(0, 40) || 'New Chat';
  }
}
