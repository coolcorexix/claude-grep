import React from 'react';
import {
  FlatList,
  Pressable,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { FoodCard } from '../components/FoodCard';
import { colors, font, radius, spacing } from '../theme';
import type { FoodEntry } from '../types';

type Props = {
  entries: FoodEntry[];
  loading: boolean;
  onAdd: () => void;
  onOpen: (entry: FoodEntry) => void;
};

export function CollectionScreen({ entries, loading, onAdd, onOpen }: Props) {
  const insets = useSafeAreaInsets();

  return (
    <View style={[styles.container, { paddingTop: insets.top + spacing.md }]}>
      <View style={styles.header}>
        <Text style={styles.kicker}>🍽️ Yum Quest</Text>
        <Text style={styles.title}>Foods unlocked</Text>
        <Text style={styles.subtitle}>
          {entries.length === 0
            ? 'The eating adventure starts here.'
            : `${entries.length} food${entries.length === 1 ? '' : 's'} discovered so far!`}
        </Text>
      </View>

      {entries.length === 0 && !loading ? (
        <View style={styles.empty}>
          <Text style={styles.emptyEmoji}>🥑</Text>
          <Text style={styles.emptyTitle}>No foods unlocked yet</Text>
          <Text style={styles.emptyText}>
            Tap the button below to snap a photo of the first food your little
            one tries.
          </Text>
        </View>
      ) : (
        <FlatList
          data={entries}
          keyExtractor={(item) => item.id}
          numColumns={2}
          columnWrapperStyle={styles.row}
          contentContainerStyle={[
            styles.listContent,
            { paddingBottom: insets.bottom + 96 },
          ]}
          showsVerticalScrollIndicator={false}
          renderItem={({ item }) => <FoodCard entry={item} onPress={onOpen} />}
        />
      )}

      <View style={[styles.fabWrap, { paddingBottom: insets.bottom + spacing.md }]}>
        <Pressable
          style={({ pressed }) => [styles.fab, pressed && styles.fabPressed]}
          onPress={onAdd}
        >
          <Text style={styles.fabText}>＋ Unlock a food</Text>
        </Pressable>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.background,
  },
  header: {
    paddingHorizontal: spacing.xl,
    paddingBottom: spacing.lg,
  },
  kicker: {
    fontSize: font.small,
    fontWeight: '700',
    color: colors.primary,
    letterSpacing: 0.5,
    textTransform: 'uppercase',
  },
  title: {
    fontSize: font.title,
    fontWeight: '800',
    color: colors.text,
    marginTop: spacing.xs,
  },
  subtitle: {
    fontSize: font.body,
    color: colors.textMuted,
    marginTop: spacing.xs,
  },
  listContent: {
    paddingHorizontal: spacing.lg,
    gap: spacing.lg,
  },
  row: {
    gap: spacing.lg,
  },
  empty: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: spacing.xxl,
  },
  emptyEmoji: {
    fontSize: 64,
    marginBottom: spacing.md,
  },
  emptyTitle: {
    fontSize: font.heading,
    fontWeight: '800',
    color: colors.text,
    marginBottom: spacing.sm,
  },
  emptyText: {
    fontSize: font.body,
    color: colors.textMuted,
    textAlign: 'center',
    lineHeight: 22,
  },
  fabWrap: {
    position: 'absolute',
    left: 0,
    right: 0,
    bottom: 0,
    paddingHorizontal: spacing.xl,
  },
  fab: {
    backgroundColor: colors.primary,
    borderRadius: radius.pill,
    paddingVertical: spacing.lg,
    alignItems: 'center',
    shadowColor: colors.primaryDark,
    shadowOpacity: 0.4,
    shadowRadius: 12,
    shadowOffset: { width: 0, height: 6 },
    elevation: 6,
  },
  fabPressed: {
    backgroundColor: colors.primaryDark,
    transform: [{ scale: 0.99 }],
  },
  fabText: {
    color: colors.white,
    fontSize: font.body,
    fontWeight: '800',
  },
});
