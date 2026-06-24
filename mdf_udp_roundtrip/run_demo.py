import threading
import time

import compare
import make_mdf
import udp_receiver
import udp_sender


def main():
    make_mdf.main()
    print()

    box = {}
    receiver = threading.Thread(target=lambda: box.update(rows=udp_receiver.receive()))
    receiver.start()
    time.sleep(0.3)

    udp_sender.main()
    receiver.join()
    udp_receiver.write_csv(box["rows"])

    print()
    compare.main()


if __name__ == "__main__":
    main()
