# frozen_string_literal: true

# This plugin's tests, run with `trmnlp test` (add `--report report` for a page of every screen it drew).
# Docs: https://github.com/usetrmnl/trmnlp#testing-plugins
# The calendar data has the shape TRMNL's CalDAV plugin shares through Plugin Merge (as Pretty Calendar reads it):
# a flat list of events with ISO start_full/end_full in local time. It is made up: an event that already ended, one past
# midnight that CalDAV flags all-day, a date-only birthday, a midnight-to-midnight trip, a cancelled event.
RSpec.describe 'Blossom Calendar' do
  def fixture(name) = JSON.parse(File.read(File.join(__dir__, 'fixtures', name)))

  let(:now) { Time.utc(2026, 11, 9, 7) } # Monday 08:00 in Berlin
  let(:variables) { { 'trmnl' => { 'user' => { 'utc_offset' => 3600, 'time_zone_iana' => 'Europe/Berlin', 'locale' => 'de' } } } }
  let(:caldav) { fixture('caldav.json') }

  def text_of(html) = html.gsub(%r{<(style|script)\b.*?</\1>}m, '').gsub(/<[^>]+>/, ' ').gsub('&nbsp;', ' ').gsub(/\s+/, ' ')

  describe 'without a calendar' do
    # Every view on TRMNL's devices and every select option, checked for page errors and leaked values.
    it_behaves_like 'a publishable recipe'

    it 'shows example events and says so' do
      text = text_of(trmnl.render(now:, variables:).html)
      expect(text).to include('Beispieltermine', 'Brunch bei Oma', 'Mädelsabend')
    end
  end

  describe 'with a calendar plugin' do
    # TRMNL hands the chosen plugin's data over under its name ("caldav_1"), which the field holds.
    let(:data) { { 'caldav_1' => caldav } }
    let(:custom_fields) { { 'calendar_1' => 'caldav_1' } }

    it_behaves_like 'a publishable recipe'

    def html(**inputs) = trmnl.render(now:, variables:, data:, custom_fields:, **inputs).html
    def text(**) = text_of(html(**))

    it 'lists today from now on, with times' do
      t = text
      expect(t).to include('Zahnarzt 14:30 – 15:15', 'Yoga, Studio Blume 18:00 – 19:00', 'ab 22:00')
      expect(t).not_to include('Frühsport')
      expect(t).not_to include('Beispieltermine')
    end

    it 'carries an event past midnight over, though CalDAV flags it all-day' do
      expect(text).to include('bis 02:00')
    end

    it 'shows all-day and multi-day events' do
      t = text
      expect(t).to include('Geburtstag von Emma ganztags', 'Wellness-Wochenende Tag 1 von 3')
    end

    it 'leaves out cancelled events' do
      expect(text).not_to include('Abgesagt')
    end

    it 'marks busy days of the month and today' do
      h = html
      %w[10 12 13 14 16 27].each { |d| expect(h).to include(%(bc-busy">#{d}<)) }
      expect(h).not_to include('bc-busy">11<')
      expect(h).to match(%r{bc-month__today">.*?</svg>\S*9</span>})
    end

    it 'follows the plugin\'s 12-hour clock and week start' do
      us = { 'caldav_1' => caldav.merge('time_format' => 'am/pm', 'first_day' => 0) }
      t = text_of(trmnl.render(now:, variables:, data: us, custom_fields:).html)
      expect(t).to include('2:30 PM – 3:15 PM')
      expect(t).to match(/S M D M D F S/)
    end

    it 'reads the data under the field\'s own name too, as Pretty Calendar does' do
      direct = { 'calendar_1' => caldav }
      expect(text_of(trmnl.render(now:, variables:, data: direct, custom_fields:).html)).to include('Zahnarzt')
    end

    it 'joins a second calendar' do
      second = { 'events' => [{ 'summary' => 'Tanzkurs', 'start_full' => '2026-11-09T17:00:00+01:00',
                                'end_full' => '2026-11-09T18:00:00+01:00', 'all_day' => false }] }
      t = text_of(trmnl.render(now:, variables:, data: data.merge('google_calendar_2' => second),
                               custom_fields: custom_fields.merge('calendar_2' => 'google_calendar_2')).html)
      expect(t.index('Zahnarzt')).to be < t.index('Tanzkurs')
      expect(t.index('Tanzkurs')).to be < t.index('Yoga, Studio Blume')
    end

    it 'says what to do when the plugin has sent nothing yet' do
      t = text_of(trmnl.render(now:, variables:, data: {}, custom_fields:).html)
      expect(t).to include('Noch keine Termine vom Kalender-Plugin')
    end
  end
end
