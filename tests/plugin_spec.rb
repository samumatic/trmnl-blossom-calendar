# frozen_string_literal: true

# This plugin's tests, run with `trmnlp test` (add `--report report` for a page of every screen it drew).
# Docs: https://github.com/usetrmnl/trmnlp#testing-plugins
# The fixture is a made-up calendar: weekly, yearly and monthly repeats, an exception, a moved date, a cancelled
# event, an Outlook zone name, an event past midnight and a multi-day trip.
RSpec.describe 'Blossom Calendar' do
  let(:ics) { File.read(File.join(__dir__, 'fixtures', 'example.ics')) }
  let(:url) { 'https://calendar.example.com/private-abc/basic.ics' }
  let(:now) { Time.utc(2026, 11, 9, 7) } # Monday 08:00 in Berlin
  let(:variables) { { 'trmnl' => { 'user' => { 'time_zone_iana' => 'Europe/Berlin', 'locale' => 'de' } } } }

  describe 'without a link' do
    # Every view on TRMNL's devices, a failing API and every select option, checked for page errors and leaked values.
    it_behaves_like 'a publishable recipe'

    it 'shows example events and says so' do
      cal = trmnl.transform(now:, variables:).data['cal']
      expect(cal['demo']).to be(true)
      expect(cal['days'].first['events'].map { it['title'] }).to include('Yoga mit Mia')
    end
  end

  describe 'with a calendar link' do
    let(:custom_fields) { { 'ics_urls' => url } }
    let(:mocks) { { url => { body: ics, headers: { 'content-type' => 'text/calendar; charset=utf-8' } } } }

    it_behaves_like 'a publishable recipe'

    def days = trmnl.transform(now:, mocks:, custom_fields:, variables:).data['cal']['days']
    def titles(day) = days.find { it['iso'] == day }&.dig('events')&.map { it['title'] }

    it 'reads today with times in Berlin, Outlook zones included' do
      today = days.first
      expect(today['events'].map { it['title'] }).to eq(['Zahnarzt', 'Yoga, Studio Blume',
                                                         'Geburtstagsparty mit einem sehr langen Titel, der nicht in eine Zeile passt'])
      expect(today['events'][0].values_at('start', 'end').map { it['t24'] }).to eq(%w[14:30 15:15])
      expect(today['events'][2]).to include('open' => true)
    end

    it 'repeats, skips and moves events' do
      expect(titles('2026-11-10')).to eq(['Geburtstag von Emma', 'Geburtstagsparty mit einem sehr langen Titel, der nicht in eine Zeile passt'])
      expect(titles('2026-11-11')).to be_nil # exception date, nothing else on
      expect(titles('2026-11-13')).to eq(['Wellness-Wochenende'])
    end

    it 'counts the days of a trip' do
      trip = days.find { it['iso'] == '2026-11-14' }['events'].first
      expect(trip['part']).to eq([3, 3])
    end

    it 'leaves out cancelled events' do
      expect(days.flat_map { it['events'] }.map { it['title'] }).not_to include('Abgesagt')
    end

    it 'marks busy days in the month, the last Friday too' do
      month = trmnl.transform(now:, mocks:, custom_fields:, variables:).data['cal']['month'].flatten
      busy = month.select { it['busy'] }.map { it['d'] }
      expect(busy).to include(2, 4, 9, 10, 12, 13, 14, 16, 27)
      expect(busy).not_to include(11)
      expect(month.find { it['today'] }['d']).to eq(9)
    end

    it 'turns webcal links into https' do
      fields = { 'ics_urls' => url.sub('https', 'webcal') }
      requests = trmnl.transform(now:, mocks:, custom_fields: fields, variables:).requests
      expect(requests.map { it[:url] }).to all(eq(url))
    end

    it 'shows a wilted blossom when the calendar is down' do
      down = { '*' => { status: 404, body: 'Not found' } }
      expect(trmnl.transform(now:, mocks: down, custom_fields:, variables:).data['cal']).to eq('ok' => false, 'error' => 'fetch')
      expect(trmnl.render(now:, mocks: down, custom_fields:, variables:)).to have_no_problems
    end
  end
end
