<script lang="ts">
	import { CircleCheck, CircleX, Waves } from 'lucide-svelte';

	import {
		getTidarrConfigQuery,
		saveTidarrConfig,
		testTidarr
	} from '$lib/queries/downloads/DownloadClientsQueries.svelte';
	import { toastStore } from '$lib/stores/toast';
	import type { TestConnectionResult, TidarrConnectionSettings } from '$lib/types';

	import DownloadClientCard from './DownloadClientCard.svelte';

	const configQuery = getTidarrConfigQuery();
	const save = saveTidarrConfig();
	const test = testTidarr();

	let enabled = $state(false);
	let url = $state('http://tidarr:8484');
	let apiKey = $state('');
	let showKey = $state(false);
	let seeded = $state(false);
	let testResult = $state<TestConnectionResult | null>(null);

	$effect(() => {
		const value = configQuery.data;
		if (value && !seeded) {
			enabled = value.enabled;
			url = value.url;
			apiKey = value.api_key;
			seeded = true;
		}
	});

	const connected = $derived(testResult?.valid === true);
	const statusText = $derived(
		connected
			? 'Connected'
			: enabled
				? url
					? 'Run Test to check the connection'
					: 'Not configured'
				: 'Disabled'
	);

	function current(): TidarrConnectionSettings {
		return { enabled, client_type: 'tidarr', url, api_key: apiKey, country_code: 'US' };
	}

	async function persist(message: string) {
		try {
			await save.mutateAsync(current());
			toastStore.show({ message, type: 'success' });
		} catch {
			toastStore.show({ message: 'Could not save Tidarr settings', type: 'error' });
			throw new Error('save failed');
		}
	}

	async function onToggle() {
		try {
			await persist(`Tidarr ${enabled ? 'enabled' : 'disabled'}`);
		} catch {
			enabled = !enabled;
		}
	}

	async function onTest() {
		try {
			testResult = await test.mutateAsync(current());
		} catch {
			testResult = { valid: false, message: "Couldn't reach Tidarr" };
		}
	}
</script>

{#if configQuery.isLoading}
	<div class="skeleton h-28 w-full rounded-box"></div>
{:else if configQuery.isError}
	<div class="alert alert-error">Failed to load Tidarr settings: {configQuery.error.message}</div>
{:else}
	<DownloadClientCard
		title="Tidarr"
		sourceLabel="Tidal"
		icon={Waves}
		{connected}
		{statusText}
		bind:enabled
		{onToggle}
		enableAriaLabel="Enable Tidarr download client"
	>
		<div class="alert alert-info text-sm">
			Tidarr keeps ownership of Tiddl, max-quality stereo FLAC, Beets, ReplayGain, naming, and the
			final library move. DroppedNeedle only submits and tracks requests.
		</div>

		<section class="space-y-3">
			<div class="form-control">
				<label class="label" for="tidarr-url"><span class="label-text">Tidarr URL</span></label>
				<input
					id="tidarr-url"
					class="input input-bordered w-full font-mono text-sm"
					bind:value={url}
					placeholder="http://tidarr:8484"
				/>
			</div>
			<div class="form-control">
				<label class="label" for="tidarr-key"><span class="label-text">API key</span></label>
				<div class="join w-full">
					<input
						id="tidarr-key"
						type={showKey ? 'text' : 'password'}
						class="input input-bordered join-item flex-1 font-mono text-sm"
						bind:value={apiKey}
						placeholder="Tidarr API key"
					/>
					<button type="button" class="btn join-item" onclick={() => (showKey = !showKey)}
						>{showKey ? 'Hide' : 'Show'}</button
					>
				</div>
			</div>
		</section>

		<div class="flex flex-wrap items-center justify-between gap-3">
			{#if testResult}
				<div class="flex items-center gap-2 text-sm {connected ? 'text-success' : 'text-error'}">
					{#if connected}<CircleCheck class="size-4" />{:else}<CircleX class="size-4" />{/if}
					{testResult.message}
				</div>
			{:else}<span></span>{/if}
			<div class="flex gap-2">
				<button class="btn btn-outline btn-sm" onclick={onTest} disabled={test.isPending}
					>{test.isPending ? 'Testing…' : 'Test'}</button
				>
				<button
					class="btn btn-primary btn-sm"
					onclick={() => persist('Tidarr settings saved')}
					disabled={save.isPending}>{save.isPending ? 'Saving…' : 'Save'}</button
				>
			</div>
		</div>
	</DownloadClientCard>
{/if}
