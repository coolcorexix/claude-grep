import AsyncStorage from '@react-native-async-storage/async-storage';
import { Directory, File, Paths } from 'expo-file-system';

import type { FoodEntry } from './types';

const ENTRIES_KEY = 'yumquest:entries:v1';

/** Directory where unlocked-food photos are kept permanently. */
function photosDir(): Directory {
  return new Directory(Paths.document, 'foods');
}

function ensurePhotosDir(): Directory {
  const dir = photosDir();
  if (!dir.exists) {
    dir.create({ intermediates: true });
  }
  return dir;
}

/** Generate a reasonably unique id without pulling in a uuid dependency. */
export function makeId(): string {
  return (
    Date.now().toString(36) + Math.random().toString(36).slice(2, 10)
  );
}

/**
 * Copy a freshly captured/picked photo (which lives in a temporary cache
 * location) into permanent app storage and return its lasting URI.
 */
export async function persistPhoto(
  tempUri: string,
  id: string
): Promise<string> {
  const dir = ensurePhotosDir();
  const source = new File(tempUri);
  // Keep the original extension when we can, default to jpg.
  const ext = source.extension && source.extension.length <= 5 ? source.extension : '.jpg';
  const dest = new File(dir, `${id}${ext}`);
  if (dest.exists) {
    dest.delete();
  }
  await source.copy(dest);
  return dest.uri;
}

/** Remove a stored photo file; ignores files that are already gone. */
export function deletePhoto(photoUri: string): void {
  try {
    const file = new File(photoUri);
    if (file.exists) {
      file.delete();
    }
  } catch {
    // Non-fatal: the diary entry is the source of truth, not the file.
  }
}

export async function loadEntries(): Promise<FoodEntry[]> {
  try {
    const raw = await AsyncStorage.getItem(ENTRIES_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as FoodEntry[];
    if (!Array.isArray(parsed)) return [];
    // Newest unlocked first.
    return parsed.sort((a, b) => b.unlockedAt.localeCompare(a.unlockedAt));
  } catch {
    return [];
  }
}

async function saveEntries(entries: FoodEntry[]): Promise<void> {
  await AsyncStorage.setItem(ENTRIES_KEY, JSON.stringify(entries));
}

export async function addEntry(input: {
  name: string;
  tempPhotoUri: string;
  note?: string;
}): Promise<FoodEntry> {
  const id = makeId();
  const photoUri = await persistPhoto(input.tempPhotoUri, id);
  const entry: FoodEntry = {
    id,
    name: input.name.trim(),
    photoUri,
    note: input.note?.trim() || undefined,
    unlockedAt: new Date().toISOString(),
  };
  const entries = await loadEntries();
  await saveEntries([entry, ...entries]);
  return entry;
}

export async function deleteEntry(id: string): Promise<void> {
  const entries = await loadEntries();
  const target = entries.find((e) => e.id === id);
  if (target) {
    deletePhoto(target.photoUri);
  }
  await saveEntries(entries.filter((e) => e.id !== id));
}
