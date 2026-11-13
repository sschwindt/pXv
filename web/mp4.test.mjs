import assert from 'node:assert/strict';
import { openAsBlob } from 'node:fs';
import { access } from 'node:fs/promises';
import test from 'node:test';
import { openMp4 } from './mp4.mjs';

const isarVideo = new URL('../examples/isar-20260804/DJI_20260804150234_0002_D.MP4', import.meta.url);

function integers(...values) {
  const bytes = Buffer.alloc(values.length * 4);
  values.forEach((value, index) => bytes.writeUInt32BE(value >>> 0, index * 4));
  return bytes;
}

function box(type, ...payload) {
  const content = Buffer.concat(payload);
  return Buffer.concat([integers(content.length + 8), Buffer.from(type), content]);
}

test('MP4 reads sample byte ranges and distinguishes decode order from presentation order', async () => {
  const media = box('mdat', Buffer.from([10, 20, 21, 30, 31, 32]));
  const avc = box('avcC', Buffer.from([1, 0x64, 0, 0x33]));
  const visual = Buffer.alloc(78);
  visual.writeUInt16BE(1920, 24);
  visual.writeUInt16BE(1080, 26);
  const table = box('stbl',
    box('stsd', integers(0, 1), box('avc1', visual, avc)),
    box('stsz', integers(0, 0, 3, 1, 2, 3)),
    box('stsc', integers(0, 1, 1, 3, 1)),
    box('co64', integers(0, 1, 0, 8)),
    box('stts', integers(0, 1, 3, 1001)),
    box('ctts', integers(0x01000000, 3, 1, 0, 1, 1001, 1, -1001)),
    box('stss', integers(0, 1, 1)));
  const movieBox = box('moov', box('trak', box('mdia',
    box('hdlr', integers(0, 0), Buffer.from('vide')),
    box('mdhd', integers(0, 0, 0, 30000, 3003)),
    box('minf', table))));
  const movie = await openMp4(new Blob([media, movieBox]));
  assert.equal(movie.config.codec, 'avc1.640033');
  assert.equal(movie.fps, 30000 / 1001);
  assert.deepEqual(movie.order.map(frame => frame.decodeIndex), [0, 2, 1]);
  assert.deepEqual(movie.order.map(({ sample }) => sample.timestamp), [0, 33367, 66733]);
  assert.deepEqual(movie.samples.map(sample => sample.offset), [8, 9, 11]);
  assert.deepEqual(movie.samples.map(sample => Boolean(sample.key)), [true, false, false]);
  const last = movie.samples[2];
  assert.deepEqual([...new Uint8Array(await movie.reader.read(last.offset, last.offset + last.size))], [30, 31, 32]);
});

test('MP4 parser identifies the actual Isar RGB frame and its exact cadence', async context => {
  try {
    await access(isarVideo);
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
    context.skip('The local Isar RGB video is absent.');
    return;
  }
  const movie = await openMp4(await openAsBlob(isarVideo));
  assert.deepEqual([movie.width, movie.height, movie.frameCount], [1920, 1080, 3884]);
  assert.equal(movie.config.codec, 'avc1.640033');
  assert.equal(movie.fps, 30000 / 1001);
  assert.equal(movie.order[1250].sample.timestamp, 41708333);
  assert.equal(movie.order[1649].sample.timestamp, 55021633);
  assert.ok(movie.samples[0].key);
});

test('QuickTime HEVC maps its profile, reversed compatibility, tier and constraints to a WebCodecs config', async () => {
  const brand = box('ftyp', Buffer.from('qt  '), integers(0), Buffer.from('qt  '));
  const sample = box('mdat', Buffer.from([10]));
  const description = Buffer.alloc(23);
  description[0] = 1;
  description[1] = 0x62; // Profile space A, high tier, profile ID 2.
  description.writeUInt32BE(0x60000000, 2);
  description[6] = 0xb0;
  description[8] = 0x08;
  description[12] = 120;
  const visual = Buffer.alloc(78);
  visual.writeUInt16BE(1920, 24);
  visual.writeUInt16BE(1080, 26);
  const table = box('stbl',
    box('stsd', integers(0, 1), box('hvc1', visual, box('hvcC', description))),
    box('stsz', integers(0, 1, 1)),
    box('stsc', integers(0, 1, 1, 1, 1)),
    box('stco', integers(0, 1, brand.length + 8)),
    box('stts', integers(0, 1, 1, 1001)));
  const movieBox = box('moov', box('trak', box('mdia',
    box('hdlr', integers(0, 0), Buffer.from('vide')),
    box('mdhd', integers(0, 0, 0, 30000, 1001)),
    box('minf', table))));
  const movie = await openMp4(new Blob([brand, sample, movieBox], { type: 'video/quicktime' }));
  assert.equal(movie.codecLabel, 'H.265');
  assert.equal(movie.config.codec, 'hvc1.A2.6.H120.B0.00.08');
  assert.deepEqual(movie.config.description, Uint8Array.from(description));
  assert.equal(movie.samples[0].offset, brand.length + 8);
});
