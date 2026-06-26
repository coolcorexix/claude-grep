import { StatusBar } from 'expo-status-bar';
import React, { useCallback, useEffect, useState } from 'react';
import { StyleSheet, View } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { CaptureScreen } from './src/screens/CaptureScreen';
import { CollectionScreen } from './src/screens/CollectionScreen';
import { DetailScreen } from './src/screens/DetailScreen';
import { loadEntries } from './src/storage';
import { colors } from './src/theme';
import type { FoodEntry } from './src/types';

type Screen =
  | { name: 'collection' }
  | { name: 'capture' }
  | { name: 'detail'; entry: FoodEntry };

export default function App() {
  const [entries, setEntries] = useState<FoodEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [screen, setScreen] = useState<Screen>({ name: 'collection' });

  const refresh = useCallback(async () => {
    const loaded = await loadEntries();
    setEntries(loaded);
    setLoading(false);
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const handleSaved = useCallback((entry: FoodEntry) => {
    setEntries((prev) => [entry, ...prev]);
    setScreen({ name: 'collection' });
  }, []);

  const handleDeleted = useCallback((id: string) => {
    setEntries((prev) => prev.filter((e) => e.id !== id));
    setScreen({ name: 'collection' });
  }, []);

  return (
    <SafeAreaProvider>
      <View style={styles.root}>
        <StatusBar style="dark" />
        {screen.name === 'collection' && (
          <CollectionScreen
            entries={entries}
            loading={loading}
            onAdd={() => setScreen({ name: 'capture' })}
            onOpen={(entry) => setScreen({ name: 'detail', entry })}
          />
        )}
        {screen.name === 'capture' && (
          <CaptureScreen
            onCancel={() => setScreen({ name: 'collection' })}
            onSaved={handleSaved}
          />
        )}
        {screen.name === 'detail' && (
          <DetailScreen
            entry={screen.entry}
            onBack={() => setScreen({ name: 'collection' })}
            onDeleted={handleDeleted}
          />
        )}
      </View>
    </SafeAreaProvider>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.background },
});
