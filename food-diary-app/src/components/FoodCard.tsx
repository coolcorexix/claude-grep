import React from 'react';
import { Image, Pressable, StyleSheet, Text, View } from 'react-native';

import { colors, font, radius, spacing } from '../theme';
import type { FoodEntry } from '../types';

type Props = {
  entry: FoodEntry;
  onPress: (entry: FoodEntry) => void;
};

function formatDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });
}

export function FoodCard({ entry, onPress }: Props) {
  return (
    <Pressable
      style={({ pressed }) => [styles.card, pressed && styles.pressed]}
      onPress={() => onPress(entry)}
    >
      <Image source={{ uri: entry.photoUri }} style={styles.photo} />
      <View style={styles.body}>
        <Text style={styles.name} numberOfLines={1}>
          {entry.name}
        </Text>
        <Text style={styles.date}>{formatDate(entry.unlockedAt)}</Text>
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  card: {
    flex: 1,
    backgroundColor: colors.surface,
    borderRadius: radius.lg,
    overflow: 'hidden',
    borderWidth: 1,
    borderColor: colors.border,
    shadowColor: '#000',
    shadowOpacity: 0.06,
    shadowRadius: 6,
    shadowOffset: { width: 0, height: 3 },
    elevation: 2,
  },
  pressed: {
    opacity: 0.85,
    transform: [{ scale: 0.98 }],
  },
  photo: {
    width: '100%',
    aspectRatio: 1,
    backgroundColor: colors.border,
  },
  body: {
    padding: spacing.md,
  },
  name: {
    fontSize: font.body,
    fontWeight: '700',
    color: colors.text,
  },
  date: {
    marginTop: 2,
    fontSize: font.small,
    color: colors.textMuted,
  },
});
