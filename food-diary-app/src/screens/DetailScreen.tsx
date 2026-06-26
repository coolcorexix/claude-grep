import React, { useState } from 'react';
import {
  Alert,
  Image,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { deleteEntry } from '../storage';
import { colors, font, radius, spacing } from '../theme';
import type { FoodEntry } from '../types';

type Props = {
  entry: FoodEntry;
  onBack: () => void;
  onDeleted: (id: string) => void;
};

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    weekday: 'long',
    month: 'long',
    day: 'numeric',
    year: 'numeric',
  });
}

export function DetailScreen({ entry, onBack, onDeleted }: Props) {
  const insets = useSafeAreaInsets();
  const [busy, setBusy] = useState(false);

  function confirmDelete() {
    Alert.alert(
      'Remove this food?',
      `"${entry.name}" will be removed from the diary. This can't be undone.`,
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Remove',
          style: 'destructive',
          onPress: async () => {
            setBusy(true);
            await deleteEntry(entry.id);
            onDeleted(entry.id);
          },
        },
      ]
    );
  }

  return (
    <View style={styles.container}>
      <ScrollView
        contentContainerStyle={{ paddingBottom: insets.bottom + spacing.xxl }}
        showsVerticalScrollIndicator={false}
      >
        <View style={styles.imageWrap}>
          <Image source={{ uri: entry.photoUri }} style={styles.image} />
          <Pressable
            style={[styles.backBtn, { top: insets.top + spacing.sm }]}
            onPress={onBack}
          >
            <Text style={styles.backText}>←</Text>
          </Pressable>
        </View>

        <View style={styles.body}>
          <Text style={styles.badge}>🏆 Unlocked</Text>
          <Text style={styles.name}>{entry.name}</Text>
          <Text style={styles.date}>{formatDate(entry.unlockedAt)}</Text>

          {entry.note ? (
            <View style={styles.noteCard}>
              <Text style={styles.noteText}>{entry.note}</Text>
            </View>
          ) : null}

          <Pressable
            style={[styles.deleteBtn, busy && styles.disabled]}
            onPress={confirmDelete}
            disabled={busy}
          >
            <Text style={styles.deleteText}>Remove from diary</Text>
          </Pressable>
        </View>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  imageWrap: { width: '100%', aspectRatio: 1, backgroundColor: colors.border },
  image: { width: '100%', height: '100%' },
  backBtn: {
    position: 'absolute',
    left: spacing.lg,
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: colors.overlay,
    alignItems: 'center',
    justifyContent: 'center',
  },
  backText: { color: colors.white, fontSize: 22, fontWeight: '700' },
  body: { padding: spacing.xl },
  badge: {
    alignSelf: 'flex-start',
    backgroundColor: colors.accent,
    color: colors.text,
    fontWeight: '800',
    fontSize: font.small,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.xs,
    borderRadius: radius.pill,
    overflow: 'hidden',
  },
  name: {
    fontSize: font.title + 4,
    fontWeight: '800',
    color: colors.text,
    marginTop: spacing.md,
  },
  date: { fontSize: font.body, color: colors.textMuted, marginTop: spacing.xs },
  noteCard: {
    backgroundColor: colors.surface,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.lg,
    marginTop: spacing.xl,
  },
  noteText: { fontSize: font.body, color: colors.text, lineHeight: 24 },
  deleteBtn: {
    marginTop: spacing.xxl,
    paddingVertical: spacing.lg,
    alignItems: 'center',
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.danger,
  },
  deleteText: { color: colors.danger, fontWeight: '700', fontSize: font.body },
  disabled: { opacity: 0.6 },
});
