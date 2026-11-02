//@api-1.0
// Offline Tier A smoke: two generation calls through the fake backend.
const config = pipeline.configuration;
const prompts = pipeline.prompts;
console.log('Starting fake Tier A smoke');
config.seed = -1;
pipeline.run({ configuration: config, prompt: prompts.prompt || 'offline smoke image one', negativePrompt: prompts.negativePrompt || '' });
console.log('First generation returned');
config.seed = 42;
pipeline.run({ configuration: config, prompt: 'offline smoke image two', negativePrompt: '' });
console.log('Second generation returned');
