//@api-1.0
// Negative smoke: this must fail before any generation attempt is created.
const config = pipeline.configuration;
config.model = '';
pipeline.run({ configuration: config, prompt: 'must not generate' });
